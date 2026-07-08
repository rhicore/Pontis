"""Spider2 navigation-detail explorer.

Unlike the BIRD schema prepare workflow, this explorer does not require every
physical table and column to receive AI-authored descriptions. It writes detail
only for the compressed navigation layer that a Spider2 agent should inspect
first: schemas, agent topics, deterministic table groups, and standalone
tables not covered by a table group.
"""

from __future__ import annotations

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

MAX_COMPLETION_ROUNDS = 4

PROMPT = """\
你是 Pontis 的 Spider2 navigation prepare agent。

当前图谱来自 Spider2-Snow。官方结构层是：

- `db`
- `schema`
- `table`
- `col`

认知导航层可能包括：

- `table_group`: deterministic extractor 识别的物理分片/版本/时间分区
- `topic`: agent explorer 创建的 schema 内语义主题分组
- `schema_landscape`: 后续 deterministic explorer 会生成的导航摘要

你的任务不是给所有物理表和所有列写 detail。Spider2 的数据库太大，这样做不可行。
但是 `topic` 不能替代 standalone table 的具体说明。topic 只是索引和调度层；
standalone table 的结构各不相同，所以每个 standalone table 仍然必须有自己的
`brief/detail`。列级 detail 暂时不是本阶段完整性目标，但 table detail 需要指出
哪些列后续值得子智能体继续展开。

## 你必须写 detail 的对象

只维护这些实体的 `brief` 和 `detail`：

1. `schema`
2. `topic`
3. `table_group`
4. standalone `table`

standalone table 指没有连接到任何 `table_group` 的物理表。

## 你不需要写 detail 的对象

- 已经被 `table_group` 覆盖的 member table；
- member table 下的 column；
- 普通 column；
- 题目级 external knowledge 文档。

你可以读取这些对象作为证据，但不要把它们作为完整性目标。

## 写作要求

用中文写。数据库、schema、table、column 原名保持英文。

### schema.detail

说明这个 schema 是什么命名空间/数据源边界，下面有哪些主要 topic、table_group、standalone table，后续 agent 应该先看哪些对象。

### topic.detail

说明 topic 的语义边界、覆盖哪些 table_group / standalone table、共同行粒度或数据对象、不应混入哪些相近对象、后续如何展开。

topic.detail 必须明确：topic 不是足够直接写 SQL 的完整 schema 摘要。它要告诉后续
agent 如何把这个 topic 作为调度入口使用：

- 用户问题出现哪些业务词、数据对象或分析动作时，应进入该 topic；
- 应优先查看哪些 table_group / standalone table；
- 哪些 standalone table 仍需读取自己的 table detail；
- 如果需要更细分析，应该如何调用子智能体在本 topic 范围内补充表/关键列理解；
- 哪些相近 topic 或 schema 不应混用。

### table_group.detail

说明这个表组代表的逻辑对象、成员命名模式、时间/版本/地区/染色体等分片维度、代表成员、common_columns / variable_columns、何时需要展开 member tables。

### standalone table.detail

说明该表每行代表什么、核心列是什么、它为什么没有被 table_group 覆盖、它与同 schema 下 topic/table_group 的关系。
standalone table.detail 必须能让后续 agent 判断这张表是否相关，至少包含：

1. 行粒度或主数据对象；
2. 核心列/疑似主键/时间列/外部 id 或连接列；
3. 该表在所属 topic 中的角色，例如事实表、维表、映射表、字典表、事件表、配置表；
4. 需要子智能体进一步查看哪些关键列，尤其是名字不透明、值域重要、可能参与 join/filter/order 的列；
5. 不应把该表误当成哪些相近表。

## 证据优先级

1. 节点已有 official 字段，例如 `official_table_description`、`official_column_description`
2. table_group 的 `common_columns`、`variable_columns`、`representative_members`
3. 表名、列名、DDL
4. 少量 query 验证

不要为了写得像领域百科而编造外部知识。

## 完成条件

所有待办的 `schema/topic/table_group/standalone table` 都必须有非空 `brief` 和 `detail`。
其中 standalone table detail 不能因为已有 topic detail 而跳过；topic detail 只负责
路由和调度，standalone table detail 负责表级理解。
完成后回复 `DONE`。
"""


def generate(workspace: Workspace) -> dict:
    """Prepare Spider2 compressed navigation descriptions."""

    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured, skipping Spider2 navigation prepare")
        return {}

    logger.info("=== Agent Spider2 Navigation Prepare ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "update_meta",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)
    agent.chat(PROMPT)

    for round_no in range(1, MAX_COMPLETION_ROUNDS + 1):
        missing = _missing_navigation_descriptions(workspace)
        if not missing:
            logger.info("Spider2 navigation completeness check passed")
            logger.info("=== Agent Spider2 Navigation Prepare done ===")
            return _preprocess_metrics(agent)

        logger.info(
            "Spider2 navigation check found %s missing entities; asking agent to finish (round %s/%s)",
            len(missing),
            round_no,
            MAX_COMPLETION_ROUNDS,
        )
        agent.chat(_completion_prompt(missing, round_no))

    missing = _missing_navigation_descriptions(workspace)
    if missing:
        sample = "\n".join(f"- {item}" for item in missing[:80])
        raise RuntimeError(
            "Spider2 navigation prepare 未通过完整性校验；剩余 "
            f"{len(missing)} 个 schema/topic/table_group/standalone table 缺少 brief 或 detail。\n"
            f"缺失实体：\n{sample}"
        )
    logger.info("=== Agent Spider2 Navigation Prepare done ===")
    return _preprocess_metrics(agent)


def _missing_navigation_descriptions(workspace: Workspace) -> list[str]:
    rows = workspace.cypher(
        """
        MATCH (n:schema)
        WHERE n._ref IS NOT NULL
          AND (n.brief IS NULL OR trim(toString(n.brief)) = ''
               OR n.detail IS NULL OR trim(toString(n.detail)) = '')
        RETURN n._ref AS ref, 'schema' AS kind
        UNION
        MATCH (n:topic)
        WHERE n.name IS NOT NULL
          AND (n.brief IS NULL OR trim(toString(n.brief)) = ''
               OR n.detail IS NULL OR trim(toString(n.detail)) = '')
        RETURN coalesce(n._ref, n.name) AS ref, 'topic' AS kind
        UNION
        MATCH (n:table_group)
        WHERE n._ref IS NOT NULL
          AND (n.brief IS NULL OR trim(toString(n.brief)) = ''
               OR n.detail IS NULL OR trim(toString(n.detail)) = '')
        RETURN n._ref AS ref, 'table_group' AS kind
        UNION
        MATCH (n:table)
        WHERE n._ref IS NOT NULL
          AND NOT (n)--(:table_group)
          AND (n.brief IS NULL OR trim(toString(n.brief)) = ''
               OR n.detail IS NULL OR trim(toString(n.detail)) = '')
        RETURN n._ref AS ref, 'standalone_table' AS kind
        ORDER BY kind, ref
        """
    )
    return [f"{row.get('ref')} ({row.get('kind')})" for row in rows if row.get("ref")]


def _completion_prompt(missing: list[str], round_no: int) -> str:
    max_items = 100
    shown = missing[:max_items]
    lines = [
        "Spider2 navigation prepare 未完成：以下导航实体仍缺少 brief 或 detail。",
        "只补齐下面这些 schema/topic/table_group/standalone table。",
        "不要补 table_group member table，不要补 column。",
        "不要因为已有 topic detail 就跳过 standalone table；topic 是索引，standalone table 仍需表级 detail。",
        "",
        f"待办轮次：{round_no}",
        "待办实体：",
    ]
    lines.extend(f"- {item}" for item in shown)
    if len(missing) > max_items:
        lines.append(f"- 还有 {len(missing) - max_items} 个未列出；先完成已列出的实体。")
    lines.extend([
        "",
        "写入方式：",
        'update_meta({"ref": "<上面的 ref>", "fields": {"brief": "...", "detail": "..."}})',
        "",
        "完成后回复 DONE。",
    ])
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
