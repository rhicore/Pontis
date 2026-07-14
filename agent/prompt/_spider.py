"""Spider benchmark prompts."""


def get_spider_snow_prompt() -> str:
    return r"""## Spider2-Snow SQL 任务

当前任务来自 Spider2-Snow。你要根据用户给出的 instruction 和项目内资源写 Snowflake SQL。

### 你能看到的上下文

- 当前 Pontis Project 的 source 是 Snowflake 数据库，并保留一份本地 benchmark 资源。
- 图谱里的 `db/table/view/col` 来自 Snowflake schema；`database/` 目录里的 `DDL.csv` 和 table JSON 是补充资源，给出完整表名、列名、类型、说明和样例行。
- `documents/` 目录包含当前题目引用的 external knowledge。只有题目给出 external knowledge 时才需要读取相应文档。
- `manifest/spider2_snow_cases.jsonl` 记录当前项目内可用的 Spider2-Snow case。
- 如果 credential 有效，可以用 `query` 对当前 Snowflake 数据库做只读探索；同时使用 `find`、`grep`、`read`、`jd`、`meta` 读取本地 DDL、JSON 样例和文档。

### 写 SQL 的规则

#### 强制导航顺序

不要从全库 DDL 或全部列开始枚举。先按以下顺序缩小范围：

1. `find({"ref":"*:schema_landscape"})` 并读取 landscape detail；
2. 根据 instruction 检索并读取相关 `schema/topic`；
3. 展开命中的 `table_group` 或 standalone `table`；
4. 只在该范围内读取候选 `logical_col/col`；
5. 写 JOIN 前检查相关 `column_domain/fk/rel/disambig`，并用少量 `query` 验证仍不确定的关系。

`topic` 只是路由索引，不能代替具体表列。`table_group` 代表多个物理分片；必须根据题目中的时间、版本、地区或其他后缀条件选择正确成员。`column_domain` 只表示候选共享值域，只有 `accepted` 域或已有 `fk/rel` 才能作为连接依据，`needs_split/rejected` 不得直接用于推断 JOIN。

当命中的 topic、table group 或 standalone table 仍有大量列时，使用 `agent` 启动一个只读子智能体做列级聚焦。子智能体任务必须包含：原始 instruction、external knowledge 摘要、限定的实体 ref，以及要求返回的候选表全名、候选列、行粒度、过滤/聚合字段、连接依据和待验证问题。子智能体不写 SQL，不修改图谱；主 Agent 根据报告继续核验并生成最终 SQL。

- 使用 Snowflake SQL，不要使用 SQLite 专有函数或 SQLite 表名假设。
- 先用知识图谱导航缩小范围，再从命中表对应的 `DDL.csv` 和 table JSON 核对真实表名、完整表名、列名、类型和样例值。
- 表名优先使用 table JSON 的 `table_fullname` 或 DDL 中可执行的 Snowflake 名称；需要跨 schema 时保留完整限定名。
- 不要编造列。字段不确定时先读相关 DDL、table JSON、文档或样例。
- external knowledge 是题目的一部分；如果 instruction 引用了指标定义、窗口、公式、UDF 或特殊值含义，先读对应文档。
- 对包含 JOIN、复杂过滤、窗口或半结构化字段的候选 SQL，在最终回复前用 `query` 做一次小结果验证；确认表列可访问、类型转换和函数可执行、结果列形状符合 instruction。不要只依赖 SQL 语法解析。
- 结果列只返回 instruction 要求的答案字段；不要加入解释列。
- 只有题目要求唯一值时才使用 `DISTINCT`；只有题目要求汇总时才 `GROUP BY`。
- 需要窗口、日期、数组、半结构化字段时，按 Snowflake 语法写。

### 最终输出

最终回复只输出一个 fenced SQL code block。代码块内是一条只读 Snowflake `SELECT` 或 `WITH ... SELECT` 查询。
"""
