"""Agent Schema Prepare — summarize schema entities.

This module prepares table/column descriptions. Relation and disambiguation
entities are written by dedicated explorer modules after column-domain candidates are
available.
"""

import logging

from explorer.utils.description_contract import DESCRIPTION_CONTRACT
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

MAX_COMPLETION_ROUNDS = 6

PROMPT = f"""\
你是 Pontis 的数据库业务词典编辑。当前图谱已经有数据库、表、列、official 字段、统计和关系实体。你的任务是为每个 `table/col` 写入符合统一契约的 `brief/detail`。

{DESCRIPTION_CONTRACT}

## 工作方式

- 用 `find` 定位表，用 `meta(..., neighbor_label="col")` 展开列。
- 使用 `meta(..., property=[...])` 定向读取 official description、已有 brief/detail 和必要的格式证据。
- official description 是业务定义的首要依据；schema 名称和必要的样例只帮助理解定义。
- 用完整 source ref 调用 `update_meta` 写入 brief/detail。

## 完成检查

每张数据库表、每个数据库列都必须同时有非空 `brief` 和非空 `detail`。完成后回复 `DONE`。
"""


def generate(workspace: Workspace) -> None:
    """Prepare schema summaries."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.bird_metadata import official_metadata_note
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping schema prepare")
        return

    logger.info("=== Agent Schema Prepare ===")

    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "update_meta"],
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT + official_metadata_note(workspace.project_path))
    for round_no in range(1, MAX_COMPLETION_ROUNDS + 1):
        missing = _missing_db_descriptions(workspace)
        if not missing:
            logger.info("Schema metadata completeness check passed")
            logger.info("=== Agent Schema Prepare done ===")
            return _preprocess_metrics(agent)

        logger.info(
            "Schema metadata check found %s missing entities; asking agent to finish (round %s/%s)",
            len(missing),
            round_no,
            MAX_COMPLETION_ROUNDS,
        )
        agent.chat(_completion_prompt(missing, round_no))

    missing = _missing_db_descriptions(workspace)
    if missing:
        missing_sample = "\n".join(f"- {item}" for item in missing[:50])
        raise RuntimeError(
            "Schema Prepare 未通过完整性校验；剩余 "
            f"{len(missing)} 个数据库表/列缺少 brief 或 detail。\n"
            f"缺失实体：\n{missing_sample}"
        )
    logger.info("=== Agent Schema Prepare done ===")
    return _preprocess_metrics(agent)


def _missing_db_descriptions(workspace: Workspace) -> list[str]:
    rows = workspace.cypher(
        """
        MATCH (f:file)--(t:table)
        WHERE f.name ENDS WITH '.sqlite'
           OR f.name ENDS WITH '.db'
           OR f.name ENDS WITH '.sqlite3'
           OR f.name ENDS WITH '.duckdb'
        WITH f, t
        WHERE t.brief IS NULL OR t.brief = '' OR t.detail IS NULL OR t.detail = ''
        RETURN f.name AS file_name, t.name AS table_name, null AS column_name, 'table' AS kind
        UNION
        MATCH (f:file)--(t:table)--(c:col)
        WHERE f.name ENDS WITH '.sqlite'
           OR f.name ENDS WITH '.db'
           OR f.name ENDS WITH '.sqlite3'
           OR f.name ENDS WITH '.duckdb'
        WITH f, t, c
        WHERE c.brief IS NULL OR c.brief = '' OR c.detail IS NULL OR c.detail = ''
        RETURN f.name AS file_name, t.name AS table_name, c.name AS column_name, 'col' AS kind
        ORDER BY kind DESC, table_name, column_name
        """
    )
    missing: list[str] = []
    for row in rows:
        file_name = row.get("file_name")
        table_name = row.get("table_name")
        column_name = row.get("column_name")
        kind = row.get("kind")
        if kind == "table":
            missing.append(f"{file_name}/{table_name}")
        elif kind == "col":
            missing.append(f"{file_name}/{table_name}/{column_name}")
    return missing


def _completion_prompt(missing: list[str], round_no: int) -> str:
    max_items = 120
    shown = missing[:max_items]
    lines = [
        "Schema Prepare 未完成：以下数据库表或列仍缺少 brief 或 detail。",
        "请补齐这些实体。",
        "",
        "要求：",
        "- 只处理下面列出的待办实体。",
        "- 写入前读取目标实体 meta；必要时读取所属表或查询局部值事实。",
        "- brief/detail 只写实体自身的含义、行粒度、值格式、枚举、空值和单位。",
        "- official 标注为 not useful/not quite useful/unuseful/unused/ignore 的列，只记录这个官方标记本身。",
        "- 每个实体都必须同时有非空 `brief` 和非空 `detail`。",
        "- 完成后回复 `DONE`。",
        "",
        f"待办轮次：{round_no}",
    ]
    if shown:
        lines.append("待办实体：")
        lines.extend(f"- {item}" for item in shown)
    if len(missing) > max_items:
        lines.append(f"- 还有 {len(missing) - max_items} 个未列出；先完成已列出的实体。")
    return "\n".join(lines)


def _preprocess_metrics(agent) -> dict:
    if not hasattr(agent, "llm_metrics"):
        return {}
    metrics = agent.llm_metrics()
    return {
        "preprocess_llm_calls": int(metrics.get("llm_rounds", 0) or 0),
        "preprocess_llm_input_tokens": int(metrics.get("input_tokens", 0) or 0),
        "preprocess_llm_cached_input_tokens": int(metrics.get("cached_input_tokens", 0) or 0),
        "preprocess_llm_uncached_input_tokens": int(metrics.get("uncached_input_tokens", 0) or 0),
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }
