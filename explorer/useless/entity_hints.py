"""Agent Entity Hints — 提取消歧与数据库短事实。

该 explorer 负责增量维护 `disambig`、`hint` 实体和基础实体的
`hints` 属性。没写过相关知识时创建；已经写过时审计、修正、
去重、补边和覆盖更新。

独立执行:
    python -m explorer.entity_hints ./my_data
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是为当前数据库图谱维护可验证的 `disambig`、`hint` 实体和基础实体 `hints` 属性。

## 目标

读取当前数据库的表、列、关系、样例值、统计信息和已有 hint/disambig，提炼稳定、可验证、可复用的数据库事实。最终让下游使用者在 `meta` 表或列时，能直接看到表列边界、行粒度、格式和值域事实；在需要比较多个候选实体时，能通过邻接 `hint` / `disambig` 看到完整依据。

## 可用信息

使用这些信息源完成探索：
- `find({"ref":"*:file:db"})` 或 `find({"ref":"*:db"})`
- `find({"ref":"*:file"})`、`find({"ref":"*:csv"})`、`find({"ref":"*:text"})` 定位本地 schema/字段说明文件
- `find({"ref":"*:table"})`
- `find({"ref":"*:col"})`
- `find({"ref":"*:fk"})`
- `find({"ref":"*:rel"})`
- `find({"ref":"*:overlap"})`
- `find({"ref":"*:disambig"})`
- `find({"ref":"*:hint"})`
- 对上述实体调用 `meta`
- 对确实描述 schema、字段含义、枚举值、行粒度或数据来源边界的本地说明文件调用 `read`
- 对当前数据库调用只读 `query`

本 explorer 只写当前数据库能验证的结构和值事实。数据集评测口径、SQL 输出风格、参考答案偏好和跨题经验由其他知识层维护。

## 写作边界

- 写入内容只陈述数据库事实，不写操作指导。
- `hints` 字段只存放短事实；内容采用事实边界、格式、粒度、覆盖范围和值域差异表述。
- 消歧内容用于辨析差异；两个实体即使名称或业务主题接近，也按不同实体描述。
- 描述实体之间的可比性、覆盖范围、值域交集和结构关系；把看似接近的实体写成差异比较。

## 知识层职责

- `detail` 写实体自身含义：表/列代表什么、行粒度、单位、格式、常见值、空值或哨兵值含义。
- `hints` 写基础实体的短事实：这个实体的语境边界、行集、粒度、格式和值域特征。
- `disambig` 写容易混用的实体边界：同名、近名、相近业务词、同一自然语言词对应多个候选。
- `hint` 写多实体事实：表角色边界、字段落点边界、JOIN 后果、聚合粒度、格式差异、显示字段差异。

基础实体 `hints` 保持短而强，详细证据放到邻接 `hint` / `disambig`。

## 产出重点

覆盖下面六类高价值事实。每类事实都要写清“语境边界、结构后果、数据库证据”。

1. **row_grain**：表的一行代表什么。标注明细行、事件行、桥表关系行、快照行、月度汇总行、维表实体行。说明 `COUNT(*)`、`COUNT(DISTINCT ...)`、JOIN 后聚合会统计什么。
2. **field_landing**：自然语言词落到哪个字段。重点比较 `type/category/status/language/date/amount/text/name/id/position/points/count/rank` 等高歧义词在多个表列中的边界。
3. **fact_table_selection**：同一业务对象出现在明细表、日志表、快照表、汇总表、维表时，标注哪个表拥有目标度量、时间条件、状态条件或官方实体属性。
4. **join_consequence**：关系会怎样改变结果。标注一对多扩行、inner join 丢行、桥表重复、方向性端点、只适合补维表属性的 join。
5. **aggregation_grain**：聚合前的分组粒度和计数对象。标注按行、按唯一实体、按关系、按日期/月度、按累计快照聚合时的差异。
6. **format_risk**：值比较前需要理解的格式。标注日期/时间戳、月份编码、时长文本、文本数字、前导零、大小写枚举、缩写与全称、0/空串/NULL/特殊值。

## 写入决策

### 基础实体 `hints`

当一个事实需要在 `meta` 基础实体时稳定可见，写入该实体 `hints`。每条 hint 使用一句中文，表达数据库事实及其结构后果：
- 表 hints 覆盖 row_grain、事实表用途、JOIN 后扩行/丢行风险。
- 列 hints 覆盖字段落点边界、单位/格式、哨兵值、与近名列的区别。
- 关系相关 hints 覆盖连接方向、覆盖率、fanout、聚合影响。

调用 `update_meta` 时传入该实体完整的当前最佳 `hints` 列表。保留正确事实，改写重复、泛化、指导性或误导性的旧项。

示例：
`update_meta({"ref": "db.sqlite/table/column", "fields": {"hints": ["该列存储 YYYYMM 月份编码；日期列存储完整日期，两者时间粒度不同。"]}})`

### `disambig` 实体

当多个实体容易被同一个自然语言表达混用，创建或更新 `disambig`，并连接全部候选实体。

`disambig` 写法：
- ref: `[歧义主题]:disambig`
- brief: 50 字以内说明歧义核心。
- detail: 分候选写语义、值特征、语境边界、结构后果和证据。
- edges: 连接全部候选实体，使用路径 ref。

关键 disambig 同步给候选基础实体写一句短 `hints`，让普通 `meta` 也能看到结论。

### `hint` 实体

当知识涉及多个实体、路径、粒度或格式选择，创建或更新 `hint`，并连接相关实体。

`hint.detail` 使用固定结构：
- 主题：这条 hint 描述哪些多实体事实。
- 涉及实体：列出表、列、关系 ref。
- 语境边界：不同实体或路径对应的自然语言线索和事实范围。
- 结构后果：不同实体或路径对应的行集、过滤范围、计数对象、排序或聚合差异。
- 数据库证据：写明来自 meta、样例值、top value、统计、fk/rel/overlap 或 query 的依据。

关键 hint 同步给相关基础实体写一句短 `hints`。

## 探索流程

1. 找到当前数据库、表、列、fk、rel、overlap、已有 hint 和 disambig。
2. 如果项目包含字段说明、数据字典、schema notes 或同类本地说明文件，先 `meta` 判断其用途；只有确认它会影响实体边界理解时才用 `read` 读取相关原文。
3. 先建立表级 row_grain 草图：每张主要表的一行代表什么，主键或重复键是什么，常见 join 会扩行还是补属性。
4. 扫描高歧义列名和近名列，建立 field_landing / disambig。
5. 对事实表、日志表、快照表、汇总表、桥表和维表建立 fact_table_selection / join_consequence hint。
6. 对日期、时间、金额、数量、rank、position、points、text/name/status/type 等列抽样检查格式和值域，建立 format_risk 和 aggregation_grain hint。
7. 覆盖更新基础实体 `hints`，确认高价值结论在表/列 `meta` 时直接可见。

## 质量标准

- 每条 hint 都能回答“这个事实描述了什么结构边界”。
- 每个 disambig 都能回答“这些候选之间有哪些差异”。
- 每个 hint/disambig 都引用当前数据库中的具体证据。
- 同一事实只保留一个最清晰的位置：短结论放基础实体 `hints`，详细比较放邻接实体。
- 用肯定、具体的中文表达数据库事实。

## 实体引用规范

- 表使用路径 ref，例如 `financial.sqlite/account`。
- 列使用路径 ref，例如 `financial.sqlite/account/account_id`。
- Related 中的邻接实体使用 `主节点ref/邻接名称:分组标签`。
- overlap、rel、fk 名称里出现的 `table.column` 是实体名称；工具调用使用路径 ref 或 Related 组合 ref。
"""


def generate(workspace: Workspace) -> None:
    """提取消歧实体、hint 实体和本地 hints。"""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent entity hints")
        return

    logger.info("=== Agent Entity Hints ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "read", "query",
            "create_entity", "update_meta", "add_edge", "delete",
        ],
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent Entity Hints done ===")
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
