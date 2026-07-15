"""Agent Description Audit — final metadata review before README."""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你正在做数据库表列 description 的最后检查。你的任务是让 `table/col` 的 `brief/detail` 与 official 字段一致，并保持为实体自身的局部说明。

## 职责

- 审查 table 和 col 的 brief/detail。
- 重点处理 official 字段标记 `unuseful`、`not useful`、`unused`、`ignore` 或同类含义的列。
- 只修正已有 description 产物。
- 保留能从 schema、official 字段、样例、统计、已有关系和说明文件支持的内容。
- `brief/detail` 写成对象说明：表的用途与行粒度，或列的含义、格式、单位和枚举解释。
- 行数、cardinality、null、sample、topk、范围、成员、归属、关系端点和相邻实体由现有 metadata 与边表达。
- 完成后直接停止，不输出总结文字。

## official 标记不可用的列

official 标记为不可用的列，列自身 brief/detail 统一写成：

```text
brief: 官方标记为不可用
detail: 官方标记为不可用
```

其他表列描述提到这类列时，只保留 `<列名> 官方标记为不可用` 这一事实。

## 执行方式

- 先找出 official 字段标记为不可用的列，整理成禁用列清单。
- 逐个读取禁用列自身元数据和所属表元数据。
- 把禁用列自身和表内对它的描述统一为 official 禁用事实。

## 审查入口

- `find({"ref": "*:table"})`
- `find({"ref": "*:col"})`
- `meta({"ref": "<table 或 col 的完整 ref>"})`

## 完成条件

- official 标记不可用的列自身 brief/detail 已统一。
- 表列 description 与 official metadata 一致，并且只包含实体自身事实。
"""


def generate(workspace: Workspace) -> dict:
    """Audit generated descriptions after relation/disambiguation review."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.bird_metadata import official_metadata_note
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping description audit")
        return {}

    logger.info("=== Agent Description Audit ===")

    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "update_meta"],
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT + official_metadata_note(workspace.project_path))
    logger.info("=== Agent Description Audit done ===")
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
