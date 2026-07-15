"""Audit disambiguation knowledge and fill semantic gaps."""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)


PROMPT = """\
你是 Pontis 的 disambiguation 审核员。图中已有一批 `disambig`，每个实体应当代表一个清晰的字段选择问题，并通过边连接全部候选。你要把现有消歧义整理成最小、准确、互不重复的知识集合，并补充 official metadata 和表结构能够明确支持的缺口。

## 审核标准

- 选择问题：多个表或列会被同一自然语言表达指代，但它们的对象、粒度、角色、编码、单位或覆盖范围不同。
- 完整性：实体连接该选择问题的全部候选；detail 说明用户在什么条件下选择哪种角色。
- 最小性：同一选择问题保留一个实体。成员相交但选择规则不同的实体分别保留，并在 detail 中写清各自主题。
- 稳定性：说明依赖 official description、表列结构、格式、单位和枚举语义等稳定事实；成员身份与连接结构由边表达。
- 信息分工：Related 列表负责显示候选身份，候选自身的 metadata 负责定义字段含义；disambig detail 只写共同的混淆触发词和选择规则。例如“查询实体本身时选择主键角色；按明细筛选或聚合时选择对应明细角色”。

## 工作流程

1. 用 find 列出已有 disambig，读取每个实体的 metadata 和邻接成员，按比较主题分组。
2. 对每组执行审计：补齐成员边，把说明整理成“混淆触发词 + 选择规则”；完全重复的实体合并到表达最清楚的一个，并删除其余副本。
3. 查看表列名称和 official description，补充值域候选无法发现的明显选择问题，例如代码与名称、起止端点、上下界和不同结构角色。
4. 创建或更新后重新读取实体，确认主题、说明和成员一致。

数据核验以 metadata 和图结构为准，整个任务不执行 SQL。删除只适用于已确认由另一个 disambig 完整覆盖的重复实体。

## 写入格式

创建新实体：
`create_entity({"ref": "field_choice:disambig", "meta": {"brief": "...", "detail": "..."}, "edges": [{"ref": "<列1:col>"}, {"ref": "<列2:col>"}]})`

更新说明：
`update_meta({"ref": "<disambig_ref>", "fields": {"brief": "...", "detail": "..."}})`

补齐成员：
`add_edge({"edges": [{"a": "<disambig_ref>", "b": "<候选实体>"}]})`

删除重复副本：
`delete({"ref": "<duplicate_disambig_ref>"})`

用中文写 brief 和 detail。

完成后回复 `DONE`。
"""


def generate(workspace: Workspace) -> dict:
    """Audit disambiguation entities and fill metadata-driven gaps."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping disambiguation audit")
        return {}

    logger.info("=== Agent Disambiguation Audit ===")

    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "create_entity", "update_meta", "add_edge", "delete"],
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)
    agent.chat(PROMPT)
    logger.info("=== Agent Disambiguation Audit done ===")
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
