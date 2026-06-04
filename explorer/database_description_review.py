"""Agent Database Description Review — audit schema metadata against BIRD descriptions.

This explorer is intended for BIRD-style database folders that contain
``database_description/*.csv``. It asks the preprocessing writer agent to read
the official description CSVs, compare them with existing table/column metadata,
and update only ``brief``, ``detail``, and ``hints`` where the existing metadata
is incomplete, misleading, or over-inferred.
"""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是根据当前项目中的 `database_description/*.csv` 官方说明文件，审计并修正已经写入的数据库表/列元数据。

## 目标

逐个读取 `database_description` 目录下的 CSV 原文，按文件名匹配数据库表，按 `original_column_name` 匹配数据库列。对照当前表/列的 `brief`、`detail`、`hints`，找出不全、错误、遗漏或过度猜测的地方，并用 `update_meta` 改写已有元数据。

本任务只维护当前数据库的本地元数据，不写跨数据库经验，不写 BIRD 评测风格规则。

## 官方说明优先级

- `database_description/*.csv` 是本任务的最高优先级证据。
- 如果官方说明和现有 AI 摘要冲突，必须以官方说明为准。
- 如果官方说明写了 `SAME as ...`，要解析它指向的同表列含义，并把继承关系写进 `detail`；同时必须写一条短 `hints`，说明该列继承自同表基准列，不是独立业务角色。
- 如果官方说明写了 `not useful`、`unuseful`、`not applicable`、空字段、占位含义或使用限制，要直接写进对应列的 `detail`；同时必须写一条短 `hints`，说明除非问题明确点名该列，否则不要用它扩展或替代正常业务字段。
- 如果官方说明为空，不要凭空补业务含义；可以保留已有的客观统计、样例和值格式信息，但要删除明显没有证据的功能性猜测。

## 允许修改的字段

只允许用 `update_meta` 修改已有表/列实体的：

- `brief`
- `detail`
- `hints`

不要创建新实体，不要写入额外字段，不要创建/删除边，不要改 README。

## 读取流程

1. 用 `find` 找到当前数据库文件、表、列，以及 `database_description` 下的 CSV 文件。
2. 对每个说明 CSV，先 `meta` 查看文件，再用 `read({"ref": "...csv...", "start_line": 1, "end_line": ...})` 读取官方 CSV 原文。单次最多 500 行；若文件超过 500 行，按行号分段 read，不能只看前几行后就停止。
3. 需要按 `original_column_name` 精确定位、筛选或核对 CSV 行时，才使用 `query({"ref": "...csv...", "sql": "...", "limit": ...})` 辅助；不要用 query 替代首次全文 read。
4. 对每个说明 CSV 对应的数据库表，读取表级 meta 和所有列清单。
5. 对每个 `original_column_name` 对应的列，读取当前列 `brief/detail/hints/sample/topk/cardinality/null_percentage`。
6. 比较官方说明和当前元数据，只修改确实需要修正的列或表。

## 改写标准

### brief

- brief 用一句中文概括该列的官方业务含义。
- brief 不写具体基数、空值比例、样例值。
- 如果官方说明明确该列无用或不适用，brief 要体现“无实际业务用途/不建议使用”。
- 如果官方说明为空且当前 brief 是猜测性的，brief 改成保守描述，例如“未提供官方说明的字段”或基于列名的最小客观描述。

### detail

detail 应包含：

- 官方列名或官方描述中的业务含义。
- `value_description` 中的代码值、单位、格式、限制、继承关系和注意事项。
- 当前统计/样例中能客观支持 SQL 使用的事实。
- 对现有摘要中过度推断或错误内容的修正。

detail 不要写“我认为”“可能是因为”“建议进一步确认”这类自言自语式句子。可以写“不应在无明确题意时使用该列”“该列官方说明为空，现有样例只能说明值格式，不能证明业务角色”。

### hints

只有当这个事实会改变 SQL 选择时才写 hints。允许覆盖已有 hints。

下面这些情况必须写 hints：

- 官方说明为 `not useful`、`unuseful`、`not applicable` 或同类无用/占位说明。
- 官方说明为 `SAME as ...` 或同类继承说明。
- 当前元数据曾把列写成错误业务角色，修正后需要防止下游继续按旧含义使用。
- 官方说明限定列只适用于某类行、某类实体、某种编码或某种粒度。

适合写 hints 的情况：

- 同名/近名列容易混用。
- 官方说明明确某列是无用、占位、继承自另一个列、特殊编码、特殊单位、特殊粒度。
- 现有 hint 会误导下游 agent 选择错误表、列、JOIN 或过滤条件。
- 官方说明能区分明细表、汇总表、日志表、桥表、维表、快照表或实体主表的用途。
- 官方说明能区分同一自然语言词在不同表列中的落点，例如 name/type/status/date/amount/text/id/position/points/count/rank。
- 官方说明能说明某列是输出属性、过滤属性、排序指标、聚合指标、连接键、枚举代码或显示字段。
- 官方说明能说明 JOIN 会改变行粒度、扩行、丢行，或只能用于补充维表属性。

hints 要短、可执行、面向 SQL 后果，例如：

- “官方说明为 not useful；除非问题明确点名该列，否则不要用它扩展管理员姓名。”
- “官方说明为 SAME as 1；含义继承自同表 1 号管理员字段，不是独立业务角色。”
- “该表是一行一笔实际交易；回答 transaction/payment 发生事实时优先用本表，不要用月度汇总表替代。”
- “该列是本地化显示文本；只在问题要求翻译/语言版本输出时使用，不替代 canonical name。”

不要为了覆盖而写泛泛的 hints。

## 表级修正

如果说明 CSV 反映出表的行粒度、代码列含义或字段组边界，而当前表级 `detail` 遗漏或写错，可以更新表 `brief/detail/hints`。表级改写要概括关键结构，不要逐列堆砌。

## SQL 决策提示优先级

审计时优先找能改变 schema linking 的信息，而不是重写所有列：

1. 候选表选择：同一业务对象同时出现在明细、汇总、日志、历史、桥表、维表或实体表时，说明哪些问题语境使用哪张表。
2. 候选字段落点：同一自然语言词可落到多个字段时，说明字段角色、适用语境和选错后的 SQL 后果。
3. 行粒度和聚合粒度：说明一行代表实体、事件、关系、快照、汇总还是明细记录；提示 COUNT、DISTINCT、GROUP BY 和 JOIN 的后果。
4. 输出字段边界：官方说明明确某字段是显示名、本地化文本、稳定 ID、版本 ID、枚举代码或指标值时，把会影响 SELECT 列选择的事实写进 hints。
5. 值和格式风险：说明文本数字、时间文本、日期粒度、前导零、大小写枚举、缩写/全称、NULL/0/空串/占位值的 SQL 后果。

这些提示必须来自当前数据库说明或可查询的数据库事实。不要写 benchmark 参考答案偏好、跨题经验或数据集评测风格。

## 实体引用规范

- 表使用路径 ref，例如 `california_schools.sqlite/schools`。
- 列使用路径 ref，例如 `california_schools.sqlite/schools/AdmFName3`。
- 写入前必须先读取目标实体，确认 ref 唯一。

## 完成条件

- 已读取所有 `database_description/*.csv` 的原文。
- 已审计说明 CSV 中出现的主要表和列。
- 对明显不全、错误、遗漏、过度猜测的 `brief/detail/hints` 完成修正。
- 完成后直接停止，不要输出总结性文字。
"""


def generate(workspace: Workspace) -> dict:
    """Run the database-description review explorer."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping database description review")
        return {}

    logger.info("=== Agent Database Description Review ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "read", "query",
            "update_meta",
        ],
        max_rounds=120,
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent Database Description Review done ===")
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
