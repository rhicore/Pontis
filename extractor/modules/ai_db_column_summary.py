"""AI DB Column Summary — 数据库列 AI 总结生成器

关注列本身的值特征：值域、分布、枚举模式、异常值、业务含义。
用中文输出。

独立执行：
    python -m extractor.ai_db_column_summary ./my_data
"""
import os
import logging
from typing import Optional

from storage import Store
from extractor.modules._utils import get_llm
from extractor.modules._ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store, config=None) -> None:
    logger.info("=== AI: DB column summary ===")

    llm = get_llm(config=config)
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    for ext in DB_EXTENSIONS:
        for ref in store.find_nodes(f"{ext}::*.*.*.col"):
            try:
                _generate_for_column(ref, store, llm)
            except Exception as e:
                logger.warning(f"Failed for {ref}: {e}")


def _generate_for_column(ref: str, store: Store, llm) -> bool:
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    # 已有 brief + detail 则跳过
    if meta.get("brief") and meta.get("detail"):
        return False

    # 解析实体名: [表名].[列名].[类型].col
    col_parts = entity_name.replace(".col", "").split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]
    data_type = col_parts[2]

    prompt = _build_prompt(
        table_name, col_name, data_type, meta,
        store, path,
    )

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=150)
        updates = {}
        if detail:
            updates["detail"] = detail
        if brief:
            updates["brief"] = brief

        if updates:
            store.set_meta(ref, updates)
            logger.info(f"  AI summary: {ref}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False


def _build_prompt(table: str, column: str, data_type: str, meta: dict,
                  store: Store, db_ref: str) -> str:
    # 基本信息部分
    parts = [
        f"表: {table}",
        f"列: {column}",
        f"类型: {data_type}",
    ]

    # 从 meta 中提取可用的统计信息
    cardinality = meta.get("cardinality")
    if cardinality is not None:
        parts.append(f"不同值的数量: {cardinality}")

    null_pct = meta.get("null_percentage")
    if null_pct is not None:
        parts.append(f"空值比例: {null_pct}%")

    # 数值列统计
    for key in ("min_value", "max_value", "mean_value"):
        if key in meta:
            label = {"min_value": "最小值", "max_value": "最大值", "mean_value": "平均值"}[key]
            parts.append(f"{label}: {meta[key]}")

    # 文本列统计
    for key in ("min_length", "max_length", "avg_length"):
        if key in meta:
            label = {"min_length": "最小长度", "max_length": "最大长度", "avg_length": "平均长度"}[key]
            parts.append(f"{label}: {meta[key]}")

    # 样本值（多给一些让模型判断值特征）
    samples = meta.get("sample", [])
    if samples:
        sample_str = ", ".join(str(s) for s in samples[:30])
        parts.append(f"样本值: [{sample_str}]")

    # TopK 高频值
    topk = meta.get("topk", [])
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

    info_block = "\n".join(parts)

    return f"""{info_block}

请用中文分析这个数据库列，重点关注以下方面：

1. **值特征**：值的格式、范围、枚举模式（如是否为固定枚举、是否有编码规则）
2. **分布特点**：值是否集中、是否有明显的长尾、是否有异常值
3. **业务含义**：从列名和值的特征推断这个列在业务中代表什么
4. **数据质量**：空值情况、是否有格式不一致、是否有明显的脏数据

要求：
- 不要写具体数字（如"有 1234 个不同值"），用定性描述（如"低基数"、"高区分度"）
- 输出纯文本，不要 markdown 格式
- brief 控制在 50 字以内
"""
