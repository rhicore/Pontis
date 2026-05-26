"""Agent Entity Hints — 提取消歧与数据库决策提示。

该 explorer 负责创建 `disambig` 和 `hint` 实体，并把行粒度、
谓词落点、值行为、JOIN 覆盖等局部事实写入实体的 `hints` 属性。

独立执行:
    python -m explorer.entity_hints ./my_data
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是为数据库图谱补充可直接帮助 SQL 决策的 disambig 和 hints。

## 目标

分析项目数据库中的表、列、关系、已有 disambig 和 hint，把稳定、可复用、会影响 SQL 写法的数据库事实写入图谱。

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

README、knowledge、example、pattern、lesson、rule 是其他知识层，本 explorer 专注从数据库结构和数据值提取实体级 hints。

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

对于关键 disambig，也给相关实体追加一句短 `hints`，让 `meta` 时稳定可见。

### 写入实体 `hints` 属性

当知识只描述一个表或列自身，或者是某个 disambig/hint 的局部提醒时，追加到该实体的 `hints`：
- 表的一行代表什么业务对象或时间粒度。
- 列的单位、格式、枚举语义、大小写行为、空值含义。
- 列是否是文本描述、代码、显示名、状态、金额、日期、计数或比例。
- 当前实体基于数据证据得到的使用边界。

工具示例：
`update_meta({"ref": "db.sqlite/table/column", "fields": {"hints": ["该列使用 YYYYMM 月份格式。"]}})`

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
- 推荐判断：说明不同查询语境下如何选择。
- 证据：引用 meta、sample、topk、query、fk、rel、overlap 的依据。

对于关键多实体 hint，也给相关实体追加一句短 `hints`，让 `meta` 时稳定可见。

## 探索重点

1. 行粒度：每张事实表、桥表、日志表、月度表、汇总表的一行代表什么。
2. 谓词落点：type、category、status、language、date、amount、text、name、id 等自然语言词的目标列。
3. 值行为：日期格式、单位、枚举、大小写、代码列与展示列、比例与计数。
4. JOIN 覆盖：fk/rel/overlap 支撑的 join 是否会丢行、扩行、重复计数或改变粒度。
5. 消歧边界：同名、近名、同义不同名实体在当前数据库里的客观差异。

## 必查决策面

优先寻找下列会稳定影响 SQL 的决策知识，并把结论写到相关实体的 `hints` 或邻接 `hint` 实体中：

- 事实表选择：同一业务对象同时出现在明细表、日志表、快照表、汇总表、维表时，标注各表适合回答的度量、时间条件和过滤条件。
- 指标字段选择：同一概念同时有数量、金额、积分、排名、状态、文本说明、显示名、代码值时，标注自然语言线索到列的落点。
- 聚合粒度：标注按行、按唯一实体、按桥表关系、按日期/月度、按累计快照聚合时的差异。
- 标识列边界：标注实体名称、代码、ID、显示名、文本说明和数值度量之间的语义差异。
- 时间和值格式：标注日期字符串、月份编码、圈速/时长文本、大小写枚举、缩写与全称、前导零等查询时的匹配方式。
- 连接代价：标注常见 JOIN 路径带来的丢行、扩行、重复计数、领域不兼容或只可间接关联的情况。
- 消歧实体：把会导致表/列/值选择错误的真实歧义写成 `disambig`，并给相关实体追加可见的短 `hints`。

## 工作流程

1. 用 `find({"ref":"*:file:db"})` 找数据库。
2. 用 `find` 查看表、列、fk、rel、overlap、disambig、hint。
3. 用 `meta` 阅读候选表/列/关系；必要时用 `query` 读取少量样本、top value 或计数。
4. 按写入规则创建 `disambig`、追加 `hints` 或创建 `hint` 实体。
5. 复查新建 `disambig` / `hint` 是否已经连到相关实体。

## 实体引用规范

- 表使用路径 ref，例如 `financial.sqlite/account`。
- 列使用路径 ref，例如 `financial.sqlite/account/account_id`。
- Related 中的邻接实体使用 `主节点ref/邻接名称:分组标签`。
- overlap、rel、fk 名称里出现的 `table.column` 是实体名称，不是工具 ref；工具调用使用路径 ref 或 Related 组合 ref。

## 输出质量

- hints 是事实性、可验证、可执行的短提示。
- `disambig` 实体表达容易混用的实体边界。
- `hint` 实体表达实体间决策，实体本地 `hints` 表达实体自身属性。
- 所有新增知识都有数据库证据支撑，证据写具体表列、样例值、统计或查询观察。
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
