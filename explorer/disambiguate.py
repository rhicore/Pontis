"""Agent Disambiguate — 发现语义歧义，创建 disambig 实体。

唯一职责：扫描同名/近名实体，判断语义差异，创建 disambig 实体并更新相关列的 detail。
不负责发现关系（rel）或写总结（由 analyze 处理）。

独立执行:
    python -m explorer.disambiguate ./my_data
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是分析项目中数据库实体之间的语义歧义，并把可复用的消歧知识写入图谱。

## 目标

发现名称相同、名称相近、自然语言含义相近但实际语义不同的表、列或关系，创建 `disambig` 实体，并把短提示追加到相关实体的 `hints` 属性。

## 语义歧义范围

重点处理这些候选竞争：
- 同名列在不同表中代表不同业务对象、不同粒度或不同用途。
- 近名列容易被同一个自然语言词触发，但实际含义不同。
- 名称相近的表服务于不同查询场景。
- 不同名称指向同一概念，查询时需要知道它们的替代或互补关系。
- 一个自然语言词可能落到多个表/列，例如 type、category、status、language、date、amount、text、name、id。

## 工作流程

1. 用 `find({"ref":"*:file:db"})` 找数据库。
2. 用 `find` 和 `meta` 建立全局 schema 认知：表、列、fk、overlap、rel、已有 disambig、已有 hint。
3. 按列名、近名、业务词和已有关系分组候选实体。
4. 对候选实体读取 `meta`，必要时用 `query` 查看少量实际值，确认它们的语义差异。
5. 为每个真实歧义创建一个 `disambig` 实体。
6. 给每个涉及实体追加一条简短 `hints`，让用户或 agent 在 `meta` 该实体时直接看到消歧提醒。
7. 复查本轮创建的 `disambig`，确保每个消歧实体都通过 edges 连接到所有涉及实体。

## 实体引用规范

- 表使用路径 ref，例如 `financial.sqlite/account`。
- 列使用路径 ref，例如 `financial.sqlite/account/account_id`。
- Related 中的邻接实体使用 `主节点ref/邻接名称:分组标签`。
- overlap、rel、fk 名称里出现的 `table.column` 是实体名称，不是工具 ref；工具调用使用路径 ref 或 Related 组合 ref。

## disambig 写入格式

创建实体：
`create_entity({"ref": "共同模式:disambig", "meta": {"brief": "...", "detail": "..."}, "edges": [...]})`

`brief` 用一句话说明歧义核心。`detail` 使用稳定结构：
- 候选实体：列出每个实体 ref。
- 各自语义：说明每个实体代表什么。
- 适用语境：说明自然语言中什么线索指向哪个实体。
- 验证证据：列出来自 meta、sample、topk、query 或关系的依据。

给相关实体追加本地提示：
`update_meta({"ref": "表或列路径ref", "fields": {"hints": ["一句消歧提示"]}})`

## 输出质量

- 消歧基于数据库证据。
- 每个 disambig 都有边连接到相关实体。
- hints 是短句，直接说明当前实体在歧义中的使用边界。
- 用中文写 brief、detail 和 hints。
"""


def generate(workspace: Workspace) -> None:
    """发现语义歧义，创建 disambig 实体。"""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent disambiguate")
        return

    logger.info("=== Agent Disambiguate ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "create_entity", "update_meta", "add_edge", "delete",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent Disambiguate done ===")
