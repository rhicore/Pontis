"""Agent Disambiguate Maintenance — maintain existing disambig entities.

Default extraction routes overlap candidates through
``relation_disambiguation_review``. This module is an optional maintenance pass
for already-created disambig entities.
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)


PROMPT = """\
你的任务是维护项目中已经存在的 `disambig` 实体，使消歧知识图谱更清晰。

本脚本只整理已有 `disambig`。新的 overlap 候选审查、rel 创建、rel/disambig 路由由 `relation_disambiguation_review` 负责。

## 维护目标

- 合并同一混淆维度下零碎的 pair disambig。
- 扩展缺少连接实体的 group disambig。
- 拆分混入多个混淆维度的 disambig。
- 删除重复、连接错误或结论过强的 disambig，并重建清晰 group。
- 改写含糊措辞，让每个候选实体都有明确事实边界。

## 写作边界

- 读取列时优先查看 `official_column_description` 和 `official_value_description`；它们是人工/官方标注，优先于 AI/agent 生成的 `brief/detail`。
- 写入内容只陈述数据库事实，重点比较来源、粒度、覆盖范围、编码、值域、行过滤、输出角色和连接后果。
- 每个候选实体至少写出一条与其他候选不同的事实边界。
- 使用明确差异表述：`不可互换`、`不能替代`、`不可无条件 JOIN`、`不是同一口径`、`不是同一编码体系`。
- 证据不足时写成“当前证据只支持候选审查，尚不足以建立稳定消歧结论”，并说明缺少哪类证据。

## 工作流程

1. 用 `find({"ref":"*:disambig"})` 查找已有 disambig。
2. 对每个需要维护的 disambig，用 `meta` 读取 brief/detail，并用 `find({"ref":"<disambig_ref>/*"})` 读取已连接实体。
3. 读取相关列和表的 meta，必要时用 query 查看少量实际值，确认事实边界。
4. 连接实体完整但措辞不清时，用 `update_meta` 改写 brief/detail。
5. 连接实体不完整、主题混杂或重复时，先确认新 group 的完整实体集合，再删除旧实体并用 `create_entity.edges` 重建。

## 写入格式

更新实体：
`update_meta({"ref": "<disambig_ref>", "fields": {"brief": "...", "detail": "..."}})`

重建实体：
`create_entity({"ref": "field_choice:disambig", "meta": {"brief": "...", "detail": "..."}, "edges": [{"ref": "<列1:col>"}, {"ref": "<列2:col>"}]})`

用中文写 brief 和 detail。
"""


def generate(workspace: Workspace) -> dict:
    """Maintain existing disambiguation entities."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping disambiguate maintenance")
        return {}

    logger.info("=== Agent Disambiguate Maintenance ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "create_entity", "update_meta", "delete",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)
    agent.chat(PROMPT)
    logger.info("=== Agent Disambiguate Maintenance done ===")
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
