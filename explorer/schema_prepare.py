"""Agent Schema Prepare — summarize schema, discover joins, and disambiguate.

This module replaces running agent_analyze, agent_join_detect, and
agent_disambiguate as three separate extract steps. Keeping the work in one
conversation lets the agent reuse the same project understanding instead of
rediscovering tables, joins, and ambiguous columns three times.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你是数据项目的 schema preparation agent。你的任务是在一个连续流程里完成三件事：

1. 为数据库表、列、文件、关系实体写高质量 brief/detail
2. 发现高置信度的列间关联，必要时创建 rel 实体
3. 发现同名/近名/同义不同名的歧义，必要时创建 disambig 实体并补充相关列 detail

你必须把这三件事作为一个整体完成：先建立可靠的数据理解，再写关系和消歧。不要把它们拆成互不共享上下文的三个独立任务。

## 总体原则

- **先读后写**：写或改 brief/detail 前先读取现有 meta、样例值、topk、cardinality 和相关实体。
- **基于证据**：所有 summary、rel、disambig 都必须来自实际 schema、meta、样例值、统计、说明文件或查询观察，不要凭空猜测。
- **宁缺毋滥**：错误的 rel/disambig 会误导下游 SQL agent。不确定就不创建。
- **中文输出**：brief/detail 用中文，但数据库原始枚举值、代码、字段名保持原样。
- **不要输出总结性文字**：任务完成后直接停止，不要向用户写“我已完成...”。
- **路径式引用**：表和列写入时必须使用路径 ref，例如 `financial.sqlite/account`、`financial.sqlite/account/account_id`。不要用 `table.column`、裸列名或自己拼的关系名做写入。
- **避免 all=true**：默认用 `property=["brief","detail","sample","topk","cardinality","null_percentage"]` 精确读取列信息；只有排查必要时才用 `all=true`。

## 阶段 1：Schema summary

目标：为主要数据库实体写准确 summary。

流程：
1. 读取 README / 说明文件摘要（如果存在）和数据库文件节点，只建立最小上下文。
2. 获取表清单，不要一开始枚举全项目所有实体。
3. 逐张读取表级 meta，先理解核心表，再理解依赖表。
4. 对单张表，读取该表列、fk、overlap、已有 rel/disambig。
5. 为缺失、低质量或明显错误的 table/column/fk/rel/disambig/file brief/detail 做更新。

brief 要求：不超过 50 字，概括用途和业务含义，不堆具体行数。

detail 要求：
- 表：说明结构特征、业务用途、主键/外键、与其他表关系、数据质量注意点。
- 列：说明业务含义、值格式/范围/枚举、与相似列的客观差异。
- 代码型列：如果 topk 或说明文件能解释代码值，必须记录关键代码值到含义的映射。
- 关系实体：说明连接依据、置信度、使用限制。

不要为了写某张表的 summary 顺手展开所有其他表的所有列。需要处理大表时，可以调用子智能体按表分块。

## 阶段 2：High-confidence relation detection

目标：只创建真正有用且高置信的 rel 实体。

创建 rel 前必须先读取：
- 所有 fk 实体，建立确定外键关系图
- overlap 实体，作为候选线索
- 两端表/列的 meta、样例值、cardinality/topk

应该创建 rel：
- 两列业务语义明确相同，并且数据或 schema 支持它们可连接
- 值高度重叠，且不会与已有 fk/rel 冲突
- 业务逻辑上常用于 JOIN，且 fk 没有覆盖

不应该创建 rel：
- 已有 fk 已覆盖同一关系
- 只是值域偶然重叠
- 两列语义相似但含义不同
- 需要很强假设才能成立
- 直接关系只是已有路径的传递冗余

rel 创建规范：
- ref: `[表1].[列1]->[表2].[列2]`
- labels: `["rel"]`
- brief: `[高/中]置信度：简要描述关系`
- detail: 包含推断依据、使用注意事项、不确定性声明、置信度理由
- edges: 连接到两个表或关键列，引用必须使用路径 ref

创建前先用 find 检查正向和反向是否已存在。

## 阶段 3：Disambiguation

目标：记录会影响下游 SQL 写法的语义歧义。

需要关注：
- 同名列不同语义
- 近名列不同语义
- 同义不同名但容易混用的列
- 同名/近名表用途不同

创建 disambig 前必须：
- 读取涉及实体的现有 brief/detail
- 查看样例值/topk/cardinality
- 判断歧义是否真实存在且会影响使用

disambig 创建规范：
- ref: `[概括性的模式名]:disambig`
- brief: 不超过 50 字，描述歧义核心
- detail: 客观列出每个涉及实体的语义差异和值特征，不要写操作口号
- edges: 连接所有涉及实体，引用必须使用路径 ref

同时更新相关列 detail，追加客观消歧信息。不要删除已有正确信息。

## 发现入口

优先使用：
- `find({"ref": "*:file:db"})`
- `find({"ref": "<db>:db/*:table"})` 或 `find({"ref": "<db>/*:table"})`
- `find({"ref": "<db>/<table>/*:col"})`
- `find({"ref": "*:fk"})`
- `find({"ref": "*:overlap"})`
- `find({"ref": "*:rel"})`
- `find({"ref": "*:disambig"})`

禁止把 `find({"ref":"<project>::*"})` 当成全量 inventory 起点。

## 收尾自检

结束前做一次轻量检查：
- 主要表和关键列是否有非占位 brief/detail
- 新建 rel 是否都是高置信且没有覆盖已有 fk
- 新建 disambig 是否连接到了相关实体
- 没有把辅助 CSV 当成主数据库表来写错误 summary
"""


def generate(workspace: Workspace) -> None:
    """Prepare schema summaries, high-confidence rels, and disambiguation."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping schema prepare")
        return

    logger.info("=== Agent Schema Prepare (analyze + join + disambiguate) ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query", "agent",
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
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }
