"""Agent Schema Prepare — summarize schema and discover high-confidence joins.

This module prepares the structural layer of the project graph. Disambiguation
entities are written by explorer.disambiguate after schema summaries and
relation candidates are available.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你是数据项目的 schema preparation agent。你的任务是维护表、列、文件、关系实体的事实性 brief/detail，并补充高置信列间 rel。

## 原则

- 先读后写：写入前读取目标实体的 meta、样例、topk、cardinality、null_percentage 和相关 fk/overlap/rel。
- 基于证据：只记录 schema、统计、样例、说明文件或查询观察能支持的事实。
- 中文写作；数据库原始字段名、枚举值、代码值保持原样。
- 表和列写入使用路径 ref，例如 `financial.sqlite/account`、`financial.sqlite/account/account_id`。
- 完成后直接停止，不输出总结文字。

## brief/detail

- brief 不超过 50 字，概括实体的事实性角色。
- 表 detail 记录行粒度、核心字段、主键/外键和与其他表的结构关系。
- 列 detail 记录业务角色、值格式、范围、枚举、单位、空值和值域事实。
- 近名字段按来源、粒度、覆盖范围、格式和值域分别描述差异。
- 代码型列在 topk、样例或说明文件能支持时记录代码值映射。

## rel

只在证据充分时创建 rel：
- 两端列的名称、表角色、值重叠、外键或说明文件共同支持关联。
- 该关联没有被已有 fk/rel 覆盖。
- detail 记录连接依据、两端结构差异和置信度理由。

创建前读取所有 fk、overlap、rel，并用 find 检查正反向 rel 是否已存在。

## 读取入口

- `find({"ref": "*:file:db"})`
- `find({"ref": "<db>:db/*:table"})` 或 `find({"ref": "<db>/*:table"})`
- `find({"ref": "<db>/<table>/*:col"})`
- `find({"ref": "*:fk"})`
- `find({"ref": "*:overlap"})`
- `find({"ref": "*:rel"})`

结束前轻量检查主要表、关键列和新建 rel 的 brief/detail。
"""


def generate(workspace: Workspace) -> None:
    """Prepare schema summaries and high-confidence rels."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping schema prepare")
        return

    logger.info("=== Agent Schema Prepare (summary + join) ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "read", "query",
            "create_entity", "update_meta", "add_edge", "delete",
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
