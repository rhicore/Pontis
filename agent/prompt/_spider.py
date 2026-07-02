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

- 使用 Snowflake SQL，不要使用 SQLite 专有函数或 SQLite 表名假设。
- 优先从 `DDL.csv` 和 table JSON 读取真实表名、完整表名、列名、类型、样例值，再写 SQL。
- 表名优先使用 table JSON 的 `table_fullname` 或 DDL 中可执行的 Snowflake 名称；需要跨 schema 时保留完整限定名。
- 不要编造列。字段不确定时先读相关 DDL、table JSON、文档或样例。
- external knowledge 是题目的一部分；如果 instruction 引用了指标定义、窗口、公式、UDF 或特殊值含义，先读对应文档。
- 结果列只返回 instruction 要求的答案字段；不要加入解释列。
- 只有题目要求唯一值时才使用 `DISTINCT`；只有题目要求汇总时才 `GROUP BY`。
- 需要窗口、日期、数组、半结构化字段时，按 Snowflake 语法写。

### 最终输出

最终回复只输出一个 fenced SQL code block。代码块内是一条只读 Snowflake `SELECT` 或 `WITH ... SELECT` 查询。
"""
