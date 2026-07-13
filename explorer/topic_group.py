"""Agent topic grouping explorer.

This explorer creates semantic ``topic`` nodes as an agent-authored navigation
layer. It does not change official schema/table/column structure and does not
rewrite deterministic ``table_group`` facts.
"""

from __future__ import annotations

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)
LARGE_SCHEMA_MIN_LOGICAL_UNITS = 20
MAX_COMPLETION_ROUNDS = 3

PROMPT = """\
你是 Pontis 的 topic grouping explorer。当前图谱已经有官方结构层：

- `db`
- `schema`
- `table`
- `col`

也可能已经有确定性认知分组：

- `table_group`

你的任务是创建 agent-authored 的 `topic` 节点，用来表达同一个 schema 下的业务/语义主题分组。
`topic` 是路由索引和任务切分单位，不是表结构摘要的替代品。后续 agent 仍然需要阅读
topic 下具体 standalone table 或 table_group 的 detail，不能只看 topic 就直接写 SQL。

## 核心原则

- `schema` 是 Snowflake 官方 namespace；不要删除或改写 `schema -> table` 官方结构。
- `table_group` 是 deterministic extractor 识别的物理分片/版本/时间分区；不要改写它。
- `topic` 是你创建的语义导航层；它不是官方表结构。
- `topic` 只负责帮助 agent 找到相关表组/表，并把后续分析任务分发给更小范围的子智能体。
- 不要把 topic 写成覆盖所有表列细节的长摘要；不同 standalone table 的结构差异必须保留到表级 detail。
- 同一个表或 table_group 可以连接到多个 topic，但只有确实有多重语义时才这么做。
- 不要为了凑数量创建 topic；小 schema 可以不创建 topic。

## 何时创建 topic

优先处理一阶认知实体过多的 schema：

- standalone table 数量很多；
- table_group 数量很多；
- schema 内同时存在多个明显主题，例如 clinical、biospecimen、metadata、assay、claim、citation、campaign finance transaction 等。

可以跳过这些情况：

- schema 只有少量表；
- 一个 table_group 已经覆盖了几乎所有表，且主题单一；
- 表名只能形成极弱猜测，没有足够证据。

## 分组对象

topic 应连接到这些已有实体：

- 所属 `schema`；
- 相关 `table_group`；
- 没被 table_group 覆盖的 standalone `table`。

不要直接连接所有 member tables，除非某个 member table 本身是 topic 的例外重点。优先连接 table_group。

## 证据

创建 topic 前先读取候选 schema、table_group 和 table 的 meta。需要时读取列名和 official description。只基于当前图谱事实，不要引入外部领域知识臆测。

## 写入格式

创建 topic：

```text
create_entity({
  "ref": "<db>.<schema>.<topic_key>:topic:knowledge",
    "meta": {
      "brief": "...",
      "detail": "...",
      "topic_key": "...",
      "topic_label": "...",
      "grouping_method": "agent_semantic_topic_v1",
      "source": "agent",
      "scope": "schema",
    "confidence": "high|medium|low",
    "agent_usage_hint": "..."
  },
  "edges": [
    {"ref": "<schema ref>"},
    {"ref": "<table_group or standalone table ref>"}
  ]
})
```

更新已有 topic 用 `update_meta`；补边用 `add_edge`。
注意：`db_ref`、`schema_ref`、`schema_name`、计数字段和边关系可以由 deterministic
landscape explorer 从连接关系推导，不要把已有图拓扑重复写进 topic 元数据。

## detail 内容

`detail` 用中文写，必须包含：

1. 这个 topic 表达的主题边界；
2. 它覆盖哪些 table_group / standalone table；
3. 这些对象共同的行粒度或数据对象；
4. 不应混入本 topic 的相近对象；
5. agent 后续如何展开。

其中“后续如何展开”必须写成调度建议，而不是直接替代下游分析：

- 看到什么类型的问题时应该进入这个 topic；
- 应先检查哪些 table_group，哪些 standalone table；
- 哪些 standalone table 必须继续读取自己的 table detail 和关键列；
- 如果 topic 下对象很多，建议如何调用子智能体分批分析，例如按事实表、维表、映射表、字典表、事件表拆分；
- 哪些情况下需要展开 table_group 的 member table 或 representative member。

## 建议粒度

每个大型 schema 通常创建 5-20 个 topic。不要把 80 个表组直接变成 80 个 topic；topic 是语义分组，不是表组别名。

完成后回复 `DONE`。
"""


def generate(workspace: Workspace) -> dict:
    """Ask an explorer agent to create schema-scoped topic nodes."""

    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured, skipping topic grouping explorer")
        return {}

    logger.info("=== Agent Topic Grouping Explorer ===")

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
    for round_no in range(1, MAX_COMPLETION_ROUNDS + 1):
        missing = _uncovered_large_schema_units(workspace)
        if not missing:
            logger.info("Topic coverage check passed")
            logger.info("=== Agent Topic Grouping Explorer done ===")
            return _preprocess_metrics(agent)
        logger.warning(
            "Topic coverage has %d uncovered logical units; completion round %d/%d",
            len(missing),
            round_no,
            MAX_COMPLETION_ROUNDS,
        )
        agent.chat(_completion_prompt(missing, round_no))

    missing = _uncovered_large_schema_units(workspace)
    if missing:
        sample = "\n".join(f"- {item}" for item in missing[:80])
        raise RuntimeError(
            "Topic grouping 未覆盖大型 schema 的全部一阶认知单元；"
            f"剩余 {len(missing)} 个。\n{sample}"
        )
    logger.info("=== Agent Topic Grouping Explorer done ===")
    return _preprocess_metrics(agent)


def _uncovered_large_schema_units(workspace: Workspace) -> list[str]:
    rows = workspace.cypher(
        """
        MATCH (s:schema)
        OPTIONAL MATCH (s)--(tg:table_group)
        WITH s, count(DISTINCT tg) AS table_group_count
        OPTIONAL MATCH (s)--(t:table)
        WHERE NOT (t)--(:table_group)
        WITH s, table_group_count, count(DISTINCT t) AS standalone_count
        WHERE table_group_count + standalone_count >= $minimum_units
        MATCH (s)--(unit)
        WHERE (unit:table_group OR (unit:table AND NOT (unit)--(:table_group)))
          AND NOT (unit)--(:topic)
        RETURN coalesce(unit._ref, unit.name) AS ref,
               s.name AS schema_name,
               CASE WHEN unit:table_group THEN 'table_group' ELSE 'standalone_table' END AS kind
        ORDER BY schema_name, kind, ref
        """,
        params={"minimum_units": LARGE_SCHEMA_MIN_LOGICAL_UNITS},
    )
    return [
        f"{row.get('ref')} ({row.get('schema_name')}/{row.get('kind')})"
        for row in rows
        if row.get("ref")
    ]


def _completion_prompt(missing: list[str], round_no: int) -> str:
    shown = missing[:100]
    lines = [
        "大型 schema 的 topic 覆盖尚未完成。以下 table_group/standalone table 没有连接任何 topic。",
        "请根据已有 topic 边界把它们补入合适 topic；确有独立主题时创建新 topic。",
        "不要把对象随意塞入语义不相干的 topic，也不要改写 schema/table_group 官方结构。",
        f"完成轮次：{round_no}",
        "",
        "未覆盖实体：",
    ]
    lines.extend(f"- {item}" for item in shown)
    if len(missing) > len(shown):
        lines.append(f"- 还有 {len(missing) - len(shown)} 个；先完成已列出的实体。")
    lines.extend([
        "",
        "已有 topic 用 add_edge 补连接；需要新 topic 时用 create_entity 并同时连接 schema 和相关认知单元。",
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
