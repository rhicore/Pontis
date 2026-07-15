"""Agent BIRD Profile — record database-backed business interpretation facts.

This explorer is intentionally agent-driven. The code only defines the task,
tools and bookkeeping; the agent must inspect the current database graph,
schema notes and sample values before deciding which hints are worth writing.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)
MAX_EXPLORER_ROUNDS = 96


PROMPT = """\
你是数据库业务口径 explorer。你的任务是探索当前数据库，把会影响查询结果解释的数据库事实写入相关表或列的本地 `hints`。

## 目标

最终 hints 帮助后来使用数据库的人判断：一行代表什么、同一对象为何重复、字段角色有什么不同、原始值如何存储，以及不同聚合口径会形成什么结果集合。

只记录能够由当前数据库 schema、official 字段、统计、样例或只读查询验证的事实。数据库没有提供证据时不推断默认 SQL 写法，也不总结数据集、参考答案或历史题目的偏好。

## 重点事实

- **行粒度**：事实表、历史表、关系表、交易表和结果表每行代表什么；表行数与关键 ID 去重数分别是多少。
- **对象标识**：记录主键、业务实体 ID、名称、代码和展示字段分别表示什么。
- **角色区别**：关系两端、主客队、owner/editor、driver/constructor、不同联系人槽位等字段的实际角色。
- **值和单位**：状态码、枚举、原始文本、日期/时间格式、金额、比例和现成指标的存储形式与单位。
- **聚合口径差异**：`COUNT(*)`、`COUNT(id)`、`COUNT(DISTINCT id)`，以及现成指标与从明细重算的结果为何不同。
- **排序候选**：用于排名的字段是否包含重复值、NULL，以及文本值和数值值是否代表不同事实。

## 探索方法

使用 `find`、`meta` 和 `query`。先读取 official 字段、brief/detail、sample、topk、cardinality 及相关 fk/rel/column_domain/disambig，再用少量查询核验尚未确定的事实。

对可能存在多行粒度的表，比较：

- 总行数与关键 ID 的不同值数量。
- 同一 ID 的重复次数。

对相近字段，写清来源、值域、单位和局部角色差异。每条 hint 只解释当前实体自身的稳定业务语义。跨实体导航、连接端点、覆盖率和未匹配行由 `fk/rel/disambig` 边及其证据表达。行数、cardinality、null 和 topk 等结构化统计用于推导语义，hints 不再抄录它们。

## 写入

`update_meta({"ref": "<表或列 ref>", "fields": {"hints": ["完整的事实 hint 列表"]}})`

只写 `hints`。读取已有 hints 后，把最终列表整理为当前数据库证据支持的事实；合并重复内容并改写无法由数据库验证的处方性结论。

每条 hint 用中文写一两句话，说明可核验的值域、格式、角色、行粒度或聚合口径差异。

## 示例

- `Player_Attributes`：每行是某球员在一个日期的属性快照，同一球员会因不同日期出现多行。
- `Laboratory.ID`：该列在检验记录中可重复；`COUNT(ID)` 表示检验行数，`COUNT(DISTINCT ID)` 表示出现过检验记录的不同患者数。
- `results.fastestLapTime` 保存展示时间字符串，`fastestLapSpeed` 保存速度数值，两列的单位和业务含义不同。
- `connected.atom_id` 与 `atom_id2` 分别表示一条连接记录的两个端点，二者值域相近但角色不同。
"""


def generate(workspace: Workspace) -> dict:
    """Explore current DB and write database-backed interpretation hints."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.bird_metadata import official_metadata_note
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping BIRD profile explorer")
        return {}

    logger.info("=== Agent BIRD Business Profile ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "update_meta",
        ],
        include_readme=True,
        max_rounds=MAX_EXPLORER_ROUNDS,
    )
    spec.meta_write_fields = ["hints"]
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT + official_metadata_note(workspace.project_path))
    logger.info("=== Agent BIRD Business Profile done ===")
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
