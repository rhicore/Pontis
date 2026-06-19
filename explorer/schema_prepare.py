"""Agent Schema Prepare — summarize schema entities.

This module prepares table/column descriptions. Relation and disambiguation
entities are written by dedicated explorer modules after overlap candidates are
available.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你是数据项目的 schema preparation agent。你的任务是维护表、列、文件实体的事实性 brief/detail。

## 原则

- 先读后写：写入前读取目标实体的 meta、样例、topk、cardinality、null_percentage 和相关 fk/overlap/rel。
- 基于证据：只记录 schema、统计、样例、说明文件或查询观察能支持的事实。
- 中文写作；数据库原始字段名、枚举值、代码值保持原样。
- 表和列写入使用路径 ref，例如 `financial.sqlite/account`、`financial.sqlite/account/account_id`。
- 完成后直接停止，不输出总结文字。

## official 字段

- `official_column_description` 和 `official_value_description` 是人工/官方标注，是列含义、值域、公式、可用性和口径的最高优先级事实。
- 列存在 official 字段时，先按 official 字段确定 brief/detail 的主语义，再补充样例、topk、统计和结构事实。
- official 字段标注 `unuseful`、`not useful`、`unused`、`ignore` 或同类含义时，brief/detail 明确写成官方标记为不用于查询语义的列；样例或 topk 只能作为原始取值事实记录，不能把该列写成推荐筛选、分组或粒度选择依据。
- 表 detail 提到列集合时，保留 official 字段给出的可用性和口径；官方标记为不用于查询语义的列，只作为存在的原始字段说明。
- 当 official 字段与已有 brief/detail 或样例推断冲突时，更新 brief/detail 使其服从 official 字段。

## brief/detail

- brief 不超过 50 字，概括实体的事实性角色。
- 表 detail 记录行粒度、核心字段、主键/外键和与其他表的结构关系。
- 列 detail 记录业务角色、值格式、范围、枚举、单位、空值和值域事实。
- 近名字段按来源、粒度、覆盖范围、格式和值域分别描述差异。
- 代码型列在 topk、样例或说明文件能支持时记录代码值映射。
- `overlap` 记录列值域的严格值交集；JOIN 依据由 fk、rel、表角色、键语义和说明文件共同确认。
- overlap/rel/disambig 是候选或邻接事实；本脚本只用它们理解表列，不创建新的 rel 或 disambig。

## 读取入口

- `find({"ref": "*:file:db"})`
- `find({"ref": "<db>:db/*:table"})` 或 `find({"ref": "<db>/*:table"})`
- `find({"ref": "<db>/<table>/*:col"})`
- `find({"ref": "*:fk"})`
- `find({"ref": "*:overlap"})`
- `find({"ref": "*:rel"})`

结束前轻量检查主要表和关键列的 brief/detail。
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
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent Schema Prepare done ===")
    return _preprocess_metrics(agent)


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
