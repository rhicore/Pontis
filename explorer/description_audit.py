"""Agent Description Audit — final metadata review before README."""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你是 description audit agent。你的任务是在 README 写作前审查当前图谱的 brief/detail，
使已有描述忠于 official 字段并保持可用于下游 SQL agent。

## 职责

- 审查 db、table、col、fk、rel、disambig 和 README 的 brief/detail。
- 重点处理 official 字段标记 `unuseful`、`not useful`、`unused`、`ignore` 或同类含义的列。
- 只修正已有 description 产物；schema 主体说明由 schema preparation 负责，README 由后续流程负责。
- 完成后直接停止，不输出总结文字。

## 禁用字段口径

official 标记为不可用的列，列自身 brief/detail 统一写成：

```text
brief: 官方标记为不用于查询，禁止分析使用
detail: 官方标记为不用于查询，禁止分析使用；不作为筛选、分组、排序、连接、行粒度判断或业务推理依据。
```

其他实体提到这类列时，只保留事实：`<列名> 官方标记为不用于查询，禁止分析使用`。

如果已有描述包含这类列的枚举值、取值分布、代码映射、值解释、业务含义、行粒度判断、连接建议或下游 SQL 用法，
改写为上面的禁用字段口径。若 rel/disambig 只围绕这类列的取值、粒度或业务用途建立，删除该实体；若实体仍覆盖其他有效字段，
保留实体并更新 detail，使其只记录有效字段边界和禁用字段事实。

## 执行方式

- 先找出 official 字段标记为不可用的列，整理成禁用列清单。
- 逐个读取禁用列自身元数据、所属表元数据、README、以及与禁用列相连的 rel/disambig。
- 对连接到禁用列的 rel/disambig，保留有效字段之间的差异说明；禁用列只写禁用事实，不展开联系人顺序、枚举、唯一值数量、代码映射或业务含义。
- 如果一个 rel/disambig 的判断基础只剩禁用列的值分析或业务用途，删除该实体；如果仍包含有效字段，改写后保留。

## 审查入口

- `find({"ref": "*:file:db"})`
- `find({"ref": "<db>:db/*:table"})` 或 `find({"ref": "<db>/*:table"})`
- `find({"ref": "<db>/<table>/*:col"})`
- `find({"ref": "*:fk"})`
- `find({"ref": "*:rel"})`
- `find({"ref": "*:disambig"})`
- `find({"ref": "README"})`

## 完成条件

- official 标记不可用的列自身 brief/detail 已统一。
- 表、库、fk、rel、disambig 和 README 中不再保留不可用列的值分析或业务用途。
- 必要的 rel/disambig 已保留，完全由不可用列分析产生的实体已删除。
"""


def generate(workspace: Workspace) -> dict:
    """Audit generated descriptions after relation/disambiguation review."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping description audit")
        return {}

    logger.info("=== Agent Description Audit ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta",
            "update_meta", "delete",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
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
