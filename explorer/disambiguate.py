"""Agent Disambiguate Maintenance — maintain and complete disambig entities.

Default extraction routes overlap candidates through
``relation_disambiguation_review``. This module maintains already-created
disambig entities and looks for obvious semantic gaps not covered by overlap
candidates.
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)


PROMPT = """\
你是 Pontis 的 disambiguate maintenance agent。当前图谱里已经有一些 `disambig` 实体，它们连接到容易混淆的表或列。
你的任务是维护已有 `disambig`，并补充明显缺失的消歧义组，让后来读取 `meta` 的人能看懂这些字段或表有什么不同。

已有消歧义主要来自列名和列值重叠。这类候选可能漏掉语义相近但没有严格关键词重叠的字段，例如字段名不同、代码列和名称列成对出现、官方说明指向同一分类体系、一个字段拆成多个端点字段、或一个字段是另一个字段的前缀/片段。
你需要在维护已有实体时，主动检查这类明显缺口，并创建清晰的 group `disambig`。

## 维护目标

- 合并同一混淆维度下零碎的 pair disambig。
- 扩展缺少连接实体的 group disambig。
- 为语义相近但没有被现有候选覆盖的字段创建新的 group disambig。
- 拆分混入多个混淆维度的 disambig。
- 修正重复、连接错误或结论过强的 disambig；保留旧实体，用说明和补充实体收窄范围。
- 改写含糊措辞，让每个已连接字段都有明确说明。

## 写作要求

- 读取列时优先查看 `official_column_description` 和 `official_value_description`；它们是人工/官方标注，优先于 AI/agent 生成的 `brief/detail`。
- 写入内容只陈述当前数据库能看到的内容，重点比较来源、粒度、覆盖范围、编码、值域、行过滤、存储类别和连接后行数变化。
- 每个已连接字段至少写出一条与其他字段不同的地方。
- 使用明确差异表述：`来源表不同`、`覆盖行不同`、`值域不同`、`稳定连接证据不足`、`不是同一编码体系`。
- 证据不足时写成“当前证据只支持候选审查，尚不足以建立稳定消歧结论”，并说明缺少哪类证据。

## 工作流程

1. 用 `find({"ref":"*:disambig"})` 查找已有 disambig。
2. 对每个需要维护的 disambig，用 `meta` 读取 brief/detail，并用 `find({"ref":"<disambig_ref>/*"})` 读取已连接实体。
3. 读取相关列和表的 meta，必要时用 query 查看少量实际值，确认事实边界。
4. 根据已有 disambig 的主题，检查同表和相关表中是否还有语义相近但未连接的字段：优先看 official description、official value description、列名、样例值、topk、代码/名称成对字段、端点字段和区间字段。
5. 连接实体完整但措辞不清时，用 `update_meta` 改写 brief/detail。
6. 连接实体不完整时，用 `add_edge` 把缺失字段补到已有 disambig。
7. 主题混杂或重复时，保留旧实体，用 `update_meta` 写清旧实体当前覆盖范围，并在需要时用 `create_entity.edges` 创建更窄的新 group。
8. 发现新的明显混淆组时，用 `create_entity.edges` 创建新的 group disambig，连接本组涉及的字段。

## 写入格式

更新实体：
`update_meta({"ref": "<disambig_ref>", "fields": {"brief": "...", "detail": "..."}})`

创建新实体：
`create_entity({"ref": "field_choice:disambig", "meta": {"brief": "...", "detail": "..."}, "edges": [{"ref": "<列1:col>"}, {"ref": "<列2:col>"}]})`

补充已有实体连接：
`add_edge({"edges": [{"a": "<disambig_ref>", "b": "<列:col>"}]})`

用中文写 brief 和 detail。

完成后回复 `DONE`。
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
            "create_entity", "update_meta", "add_edge",
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
