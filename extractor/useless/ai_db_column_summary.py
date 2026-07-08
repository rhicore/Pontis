"""AI DB Column Summary — 数据库列 AI 总结生成器

按表分组串行处理，利用 prompt caching 共享前缀。

独立执行：
    python -m extractor.ai_db_column_summary ./my_data
"""
import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from storage.workspace import Workspace
from extractor.utils.loader import Config
from extractor.utils.ai_utils import generate_with_prefix
from extractor.utils.refs import db_column_ref, db_table_ref, get_entity_meta, set_entity_meta

logger = logging.getLogger(__name__)


def _json_value(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value

COL_MAX_WORKERS = 4
TABLE_MAX_WORKERS = 4
_STYLE_RETRY_MESSAGE = """\
上一版包含操作指导或等同归纳措辞。请改写为数据库事实陈述：
- 只描述列的业务角色、值格式、值域、枚举、空值和粒度事实。
- 近名字段只描述差异。
- 不写操作步骤、使用处方、清理动作或字段替代关系。"""

_ADVICE_MARKERS = (
    "建议", "推荐", "应该", "应当", "必须", "避免使用", "清洗", "可用于",
)
_AMBIGUOUS_EQUIVALENCE_MARKERS = (
    "含义相同", "语义对应", "同一概念", "等价", "可替代",
)

_ANALYSIS_INSTRUCTIONS = """\
请用中文为数据库列写事实性摘要。

关注内容：
1. 业务角色：列名、表语境和样例共同支持的含义。
2. 值特征：格式、单位、枚举代码、文本模式、空值和值域事实。
3. 实体边界：与同表或近名字段的来源、粒度、覆盖范围、格式和值域差异。

写作要求：
- brief 控制在 20 字以内，只概括列的事实性角色。
- detail 使用陈述句记录可观察事实，不写具体行数、比例或基数数字。
- official_column_description 和 official_value_description 是人工/官方标注，优先级高于样本、统计值和 AI 总结；如果存在，brief/detail 必须与其一致。
- official 字段为空时再依据列名、样本、topk 和统计信息总结。
- 代码型列在 topk 或样例能支持时记录代码值含义。
- 近名字段只写差异，不归纳为同一含义。
- 输出纯文本，不使用 markdown。"""


def generate(workspace: Workspace, config=None) -> None:
    logger.info("=== AI: DB column summary (parallel) ===")

    llm = config.get_llm() if config else None
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(
            "MATCH (n) WHERE n.name ENDS WITH $suffix RETURN n",
            params={"suffix": ext_suffix},
        )
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            try:
                _process_database(db_ref, workspace, llm)
            except Exception as e:
                logger.warning(f"Failed for {db_ref}: {e}")

    if config and hasattr(config, "add_preprocess_token_metrics") and hasattr(llm, "metrics"):
        config.add_preprocess_token_metrics(llm.metrics())


def _process_database(db_ref: str, workspace: Workspace, llm) -> None:
    """处理一个数据库：按表分组，表与表之间也并行。"""
    # 按 table 分组
    table_groups = defaultdict(list)
    tbl_rows = workspace.cypher(
        "MATCH (d {name: $db_ref})--(t:table) RETURN t",
        params={"db_ref": db_ref},
    )
    for tbl_row in tbl_rows:
        table_ref = tbl_row["t"]["name"]
        col_rows = workspace.cypher(
            "MATCH (d {name: $db_ref})--(t {name: $table_ref})--(c:col) RETURN c",
            params={"db_ref": db_ref, "table_ref": table_ref},
        )
        for col_row in col_rows:
            col_ref = db_column_ref(db_ref, table_ref, col_row["c"]["name"])
            table_groups[table_ref].append(col_ref)

    if not table_groups:
        return

    total = 0
    table_workers = min(TABLE_MAX_WORKERS, max(1, len(table_groups)))
    with ThreadPoolExecutor(max_workers=table_workers) as executor:
        futures = {
            executor.submit(_process_table, db_ref, table_ref, col_refs, workspace, llm): table_ref
            for table_ref, col_refs in table_groups.items()
        }
        for future in as_completed(futures):
            table_ref = futures[future]
            try:
                total += future.result()
            except Exception as e:
                logger.warning(f"Failed for {db_ref}::{table_ref}: {e}")

    if total:
        logger.info(f"  AI column summary: {db_ref} ({total} cols)")


def _process_table(db_ref: str, table_ref: str, col_refs: list,
                   workspace: Workspace, llm) -> int:
    """处理一张表：构建共享前缀，并行处理各列。"""
    pending = []
    for ref in col_refs:
        meta = get_entity_meta(workspace, ref)
        if meta and not (meta.get("brief") and meta.get("detail")):
            pending.append((ref, meta))

    if not pending:
        logger.info(f"  {table_ref}: already done ({len(col_refs)} cols)")
        return 0

    logger.info(f"  {table_ref}: processing {len(pending)}/{len(col_refs)} cols")
    shared_prefix = [
        {"role": "system", "content": _ANALYSIS_INSTRUCTIONS},
        {"role": "user", "content": _build_table_info(db_ref, table_ref, workspace)},
    ]

    done = 0
    with ThreadPoolExecutor(max_workers=COL_MAX_WORKERS) as executor:
        futures = {}
        for ref, meta in pending:
            col_block = _build_column_block(ref, meta)
            futures[executor.submit(
                _process_column, ref, col_block, shared_prefix, llm, workspace)] = ref

        for future in as_completed(futures):
            ref = futures[future]
            try:
                ok = future.result()
                if ok:
                    done += 1
                    logger.info(f"  AI col done: {ref}")
                else:
                    logger.info(f"  AI col skip: {ref} (LLM returned empty)")
            except Exception as e:
                logger.warning(f"  AI col fail: {ref}: {e}")

    if done:
        logger.info(f"  AI summary: {db_ref}::{table_ref} ({done}/{len(pending)})")
    return done


def _process_column(col_ref: str, col_block: str,
                    shared_prefix: list, llm, workspace: Workspace) -> bool:
    messages = shared_prefix + [{"role": "user", "content": col_block}]
    detail, brief = generate_with_prefix(llm, messages)
    if _has_metadata_style_violation(detail) or _has_metadata_style_violation(brief):
        retry_messages = messages + [{"role": "user", "content": _STYLE_RETRY_MESSAGE}]
        detail, brief = generate_with_prefix(llm, retry_messages)

    if _has_metadata_style_violation(detail) or _has_metadata_style_violation(brief):
        logger.info(f"  AI col skip: {col_ref} (style violation)")
        return False

    updates = {}
    if detail:
        updates["detail"] = detail
    if brief:
        updates["brief"] = brief

    if updates:
        set_entity_meta(workspace, col_ref, updates)
        return True
    return False


def _has_metadata_style_violation(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _ADVICE_MARKERS + _AMBIGUOUS_EQUIVALENCE_MARKERS)


def _build_table_info(db_ref: str, table_ref: str, workspace: Workspace) -> str:
    """构建表级信息（同表所有列共用的 prompt 前缀）。"""
    parts = [f"数据库: {db_ref}", f"表: {table_ref}"]

    table_meta = get_entity_meta(workspace, db_table_ref(db_ref, table_ref)) or {}
    if table_meta.get("row_count") is not None:
        parts.append(f"行数: {table_meta['row_count']}")
    if table_meta.get("brief"):
        parts.append(f"表描述: {table_meta['brief']}")

    # 列清单
    col_lines = []
    col_rows = workspace.cypher(
        "MATCH (d {name: $db_ref})--(t {name: $table_ref})--(c:col) RETURN c",
        params={"db_ref": db_ref, "table_ref": table_ref},
    )
    for col_row in col_rows:
        col_name = col_row["c"]["name"]
        col_ref = db_column_ref(db_ref, table_ref, col_name)
        col_meta = get_entity_meta(workspace, col_ref)
        dtype = col_meta.get("col_type", "?") if col_meta else "?"
        col_lines.append(f"  {col_name} ({dtype})")
    if col_lines:
        parts.append("所有列:\n" + "\n".join(col_lines))

    # FK
    fk_rows = workspace.cypher(
        "MATCH (d {name: $db_ref})--(t {name: $table_ref})--(f:fk) RETURN f",
        params={"db_ref": db_ref, "table_ref": table_ref},
    )
    if fk_rows:
        fk_lines = []
        for fk_row in fk_rows:
            ent = fk_row["f"]["name"]
            if table_ref in ent:
                if "->" in ent:
                    sides = ent.split("->")
                    if len(sides) == 2:
                        fk_lines.append(f"  {sides[0]} → {sides[1]}")
        if fk_lines:
            parts.append("外键:\n" + "\n".join(fk_lines))

    return "\n".join(parts)


def _build_column_block(col_name: str, meta: dict) -> str:
    """构建单列的统计信息 prompt。"""
    display_name = meta.get("name", col_name)
    dtype = meta.get("col_type", "?")
    parts = [
        f"列: {display_name}",
        f"类型: {dtype}",
    ]

    official_column_description = meta.get("official_column_description")
    if official_column_description and str(official_column_description).strip():
        parts.append(f"官方列说明: {official_column_description}")

    official_value_description = meta.get("official_value_description")
    if official_value_description and str(official_value_description).strip():
        parts.append(f"官方取值说明: {official_value_description}")

    cardinality = meta.get("cardinality")
    if cardinality is not None:
        parts.append(f"不同值的数量: {cardinality}")

    null_pct = meta.get("null_percentage")
    if null_pct is not None:
        parts.append(f"空值比例: {null_pct}%")

    for key in ("min_value", "max_value", "mean_value"):
        if key in meta:
            label = {"min_value": "最小值", "max_value": "最大值", "mean_value": "平均值"}[key]
            parts.append(f"{label}: {meta[key]}")

    for key in ("min_length", "max_length", "avg_length"):
        if key in meta:
            label = {"min_length": "最小长度", "max_length": "最大长度", "avg_length": "平均长度"}[key]
            parts.append(f"{label}: {meta[key]}")

    samples = meta.get("sample", [])
    if samples:
        sample_str = ", ".join(str(s) for s in samples[:30])
        parts.append(f"样本值: [{sample_str}]")

    topk = _json_value(meta.get("topk", []))
    if topk:
        top_items = []
        for t in topk[:5]:
            v = t.get("value")
            pct = t.get("percentage")
            if pct is not None:
                top_items.append(f"{v}({pct}%)")
            else:
                top_items.append(str(v))
        parts.append(f"高频值: [{', '.join(top_items)}]")

    return "\n".join(parts)
