"""Agent Schema Prepare — summarize schema entities.

This module prepares table/column descriptions. Relation and disambiguation
entities are written by dedicated explorer modules after overlap candidates are
available.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

MAX_COMPLETION_ROUNDS = 6

PROMPT = """\
你是 Pontis 的 schema prepare agent。
当前图谱里已经有数据库文件、表、列、外键、overlap/rel 等实体。你的任务是给数据库表和列维护 `brief` 和 `detail`。

## 工作目标

后来主 agent 会通过 `meta` 读取这些说明来理解数据库。你写的内容应当让它知道：
- 表每行代表什么。
- 列表示什么、值长什么样、是否有枚举/范围/空值。
- 表和列之间已有的外键、overlap 或 rel 关系是什么。
- 相近字段分别来自哪里，粒度、覆盖范围和值格式有什么差异。

## 证据

写入前先读取目标实体的 `meta`。需要补充事实时，再读取所属表、相邻 fk/overlap/rel、说明文件，或用 `query` 核验局部字段事实。

`official_column_description` 和 `official_value_description` 是人工/官方标注，优先级最高。列被 official 标为 `unuseful`、`not useful`、`not quite useful`、`unused`、`ignore` 或同类含义时，只记录这个官方标记本身。

## 写入

- `brief` 不超过 50 字，概括实体角色。
- `detail` 写对象事实：行粒度、字段含义、值格式、枚举、范围、空值、单位、主键/外键、overlap/rel。
- 中文写作；数据库原始表名、字段名、枚举值和代码值保持原样。
- 表和列写入使用路径 ref，例如 `financial.sqlite/account`、`financial.sqlite/account/account_id`。

## 完成检查

每张数据库表、每个数据库列都必须同时有非空 `brief` 和非空 `detail`。完成后回复 `DONE`。
"""


def generate(workspace: Workspace) -> None:
    """Prepare schema summaries."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping schema prepare")
        return

    logger.info("=== Agent Schema Prepare ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "read", "query",
            "update_meta",
        ],
        include_readme=True,
        query_mode="single_table_fact_check",
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
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
        "- 写入前读取目标实体 meta；必要时读取所属表、同组代表列、相关 fk/overlap/rel/disambig 或说明文件。",
        "- brief/detail 写对象事实：含义、行粒度、值格式、枚举、空值、结构关系。",
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
