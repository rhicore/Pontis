"""Agent Entity Hints — 提取消歧与数据库决策提示。

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
你的任务是为数据库图谱维护可直接帮助 SQL 决策的 disambig 和 hints。

## 目标

分析项目数据库中的表、列、关系、已有 disambig 和 hint，把稳定、可复用、会影响 SQL 写法的数据库事实写入图谱。

同一个脚本同时支持两种情况：
- 如果当前库还没有相关知识，创建新的 `hint` / `disambig`，并给基础实体写入短 `hints`。
- 如果当前库已经有相关知识，读取已有内容，保留正确且有用的部分，修正错误或过强表述，删除重复或误导实体，补齐缺失边，并用 `update_meta` 覆盖更新基础实体 `hints`。

写入范围限定为当前数据库中可直接验证的事实：schema、样例值、top value、统计分布、外键、重叠率、实际查询结果。外部评测口径、SQL 书写偏好、题集惯例、参考答案风格属于 README 或 solver prompt 层。

## 信息源清单

本 explorer 的输入源只包括：
- `find({"ref":"*:file:db"})` 或 `find({"ref":"*:db"})`
- `find({"ref":"*:table"})`
- `find({"ref":"*:col"})`
- `find({"ref":"*:fk"})`
- `find({"ref":"*:rel"})`
- `find({"ref":"*:overlap"})`
- `find({"ref":"*:disambig"})`
- `find({"ref":"*:hint"})`
- 对上述实体的 `meta`
- 对当前数据库执行的 `query`

README、knowledge、example、pattern、lesson、rule 由其他知识层维护，本 explorer 专注从数据库结构和数据值提取实体级 hints。

## 职责边界

- `detail`：说明实体“是什么”。写表/列的稳定语义、行粒度、单位、格式、常见值、空值含义。
- `fk` / `rel` / `overlap`：说明“怎么连”和结构事实。普通结构连接保留在这些关系实体中。
- 基础实体 `hints`：只写会改变 SQL 决策的短提醒。写“适用语境、边界语境、会改变什么结果集合”。
- `disambig` 实体：多个表/列/关系容易混用时，集中写候选之间的语义边界。
- `hint` 实体：多个实体、路径或粒度之间的选择知识，例如事实表选择、字段落点、JOIN 后果、格式归一化风险。

同一事实放在最适合的知识层。基础实体 `hints` 保留一两句对 solver 有决策作用的摘要；详细比较放到 `hint` / `disambig` 实体。

## 增量维护规则

在写入任何新知识前，先用 `find({"ref":"*:hint"})`、`find({"ref":"*:disambig"})` 和相关基础实体的 `meta` 检查是否已有同类内容。

- 没有同类内容：创建新 `hint` / `disambig`，连接相关实体，并覆盖写入相关基础实体的当前最佳 `hints` 列表。
- 已有同类内容且正确：保留它；必要时用 `add_edge` 补齐缺失边，用 `update_meta` 补强 brief/detail。
- 已有同类内容但太泛、重复、错误或措辞过强：用 `update_meta` 改写，或用 `delete` 删除重复实体。
- 基础实体 `hints` 每次写入都传入完整保留列表；`update_meta` 会用提供列表替换旧 hints。
- 同义 hint 合并成一条表达；同一实体最多保留少量高价值决策 hint。

## 写入规则

### 创建 `disambig` 实体

当多个实体容易被下游 SQL agent 混用时，创建 `disambig` 实体并连到所有相关实体：
- 同名列不同语义。
- 近名列不同语义。
- 同义不同名但容易混用的列。
- 同名/近名表用途不同。
- 同一自然语言词可能落到多个不同实体，但不同语境下选择不同。

创建 disambig 前必须：
- 读取涉及实体的 `brief/detail`。
- 查看样例值、topk、cardinality、null_percentage。
- 判断歧义是否真实存在，并且会影响 SQL 写法。

disambig 创建规范：
- ref: `[概括性的模式名]:disambig`
- brief: 不超过 50 字，描述歧义核心。
- detail: 客观列出每个涉及实体的语义差异、值特征、适用语境和使用边界。
- edges: 连接所有涉及实体，引用必须使用路径 ref。

对于关键 disambig，也给相关实体覆盖写入一句短 `hints` 摘要，让 `meta` 时稳定可见。写入时必须保留该实体已有的仍然正确、有用的 hints。

### 维护实体 `hints` 属性

当知识会改变 SQL 决策、且需要在 `meta` 基础实体时直接可见时，写到该实体的 `hints`：
- 同名/近名候选字段中，本实体的使用边界。
- 使用该实体会导致的行粒度、聚合粒度或覆盖范围。
- 格式归一化、大小写、前导零、文本数值等会改变匹配集合的风险。
- 当前实体与相邻表/列相比，适合或不适合回答的查询类型。

写入 `hints` 的内容满足：
- 行数、列数、主键、外键等事实会改变 SQL 决策时写入。
- 普通语义说明优先写入 `detail`。
- 字段或路径选择结论使用数据库事实可唯一支撑的边界表达。

工具示例：
`update_meta({"ref": "db.sqlite/table/column", "fields": {"hints": ["该列使用 YYYYMM 月份格式；与 YYYY-MM-DD 日期列比较时先统一格式。"]}})`

### 创建邻接 `hint` 实体

当知识涉及两个或多个实体之间的选择、比较、JOIN、覆盖或粒度转换时，创建 `hint` 实体，并用 edges 连接所有相关实体：
- 自然语言谓词 type/category/status/language/date/amount/text/name 与数据库表列语义的落点。
- 两条 JOIN 路径的覆盖率、扩行风险或实体粒度差异。
- 多个相关表中哪个表拥有目标度量值或时间条件。
- 事实表与维表、月度表与实体表之间由数据粒度决定的聚合顺序。

工具示例：
`create_entity({"ref": "language predicate landing:hint", "meta": {"brief": "...", "detail": "..."}, "edges": [...]})`

`detail` 使用稳定结构：
- 决策问题：这条 hint 解决什么 SQL 选择。
- 涉及实体：列出表/列/关系 ref。
- 判断边界：说明不同查询语境下如何选择，以及选择错误会改变什么。
- 证据：引用 meta、sample、topk、query、fk、rel、overlap 的依据。

对于关键多实体 hint，也给相关实体覆盖写入一句短 `hints` 摘要，让 `meta` 时稳定可见。写入时必须保留该实体已有的仍然正确、有用的 hints。

## 探索重点

1. 事实表选择：同一业务对象同时出现在明细表、日志表、快照表、汇总表、维表时，区分各表适合回答的度量、时间条件和过滤条件。
2. 字段落点：type、category、status、language、date、amount、text、name、id、position、points 等自然语言词可能落到多个列时，标注候选边界。
3. JOIN 后果：fk/rel/overlap 支撑的 join 是否会丢行、扩行、重复计数、改变粒度或只用于补充维表属性。
4. 聚合粒度：按行、按唯一实体、按桥表关系、按日期/月度、按累计快照聚合时的差异。
5. 格式风险：日期字符串、月份编码、圈速/时长文本、大小写枚举、缩写与全称、前导零、文本数值等格式处理是否会改变匹配集合。
6. 显示字段竞争：name/title/type/status 等展示字段分布在多个表时，区分主表官方字段、事实表快照字段、本地化字段和代码说明字段。

## 必查决策面

优先寻找下列会稳定影响 SQL 的决策知识，并把结论写到相关实体的 `hints` 或邻接 `hint` 实体中：

- fact_table_selection：例如明细交易表 vs 月度聚合表、结果表 vs 排名快照表、当前帖子表 vs 历史记录表。
- field_landing：例如 `category/type/status/language/date/amount/text/name/position/points` 在多个表列之间的落点。
- join_consequence：例如桥表只表示关系、快照 join 会扩行、owner/role 字段需要过滤、端点方向会改变集合。
- aggregation_grain：例如 COUNT 行、COUNT DISTINCT 实体、按月份先聚合再取最大、累计快照适合表达累计状态。
- format_risk：例如前导零补齐、大小写归一化、LIKE 前缀匹配、文本时间排序、YYYYMM 与 YYYY-MM-DD 分属不同格式。
- display_field_competition：例如官方 name、快照 display name、本地化 name、代码说明 type 同时存在。

## 工作流程

1. 用 `find({"ref":"*:file:db"})` 找数据库。
2. 用 `find` 查看表、列、fk、rel、overlap、disambig、hint。
3. 用 `meta` 阅读候选表/列/关系；必要时用 `query` 读取少量样本、top value 或计数。
4. 对每个候选知识判断：已有则更新/补边/删除重复；没有则创建。
5. 用 `update_meta` 覆盖写入基础实体的完整 `hints` 列表，保留正确旧项，删除重复或误导项。
6. 复查 `disambig` / `hint` 是否已经连到相关实体，并确认基础实体 `meta` 时能看到短摘要。

## 实体引用规范

- 表使用路径 ref，例如 `financial.sqlite/account`。
- 列使用路径 ref，例如 `financial.sqlite/account/account_id`。
- Related 中的邻接实体使用 `主节点ref/邻接名称:分组标签`。
- overlap、rel、fk 名称里出现的 `table.column` 是实体名称，不是工具 ref；工具调用使用路径 ref 或 Related 组合 ref。

## 输出质量

- hints 是事实性、可验证、可执行的短提示，只表达 SQL 决策后果。
- `disambig` 实体表达容易混用的实体边界。
- `hint` 实体表达实体间决策，实体本地 `hints` 表达该实体参与该决策时的简短摘要。
- 所有新增知识都有数据库证据支撑，证据写具体表列、样例值、统计或查询观察。
- 写入当前数据库中可验证的结构和值事实，参考答案风格、题集先验或外部业务常识由其他知识层维护。
- 用中文写 brief、detail 和 hints。
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
            "find", "meta", "query",
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
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }
