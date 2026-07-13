"""SQL 生成规范 — 所有需要生成 SQL 的模式共享。

强制流程 + 常见陷阱，适用于任何 text-to-sql 任务。
"""

_SQL_RULES = r"""## 数据库 SQL 准则

目标是理解 schema、关系和题目输出契约，然后生成满足问题的 SQLite SQL。

### 工作流

1. 用定向 `find` 找数据库、表、列和关系实体。
2. 用 `meta` 确认关键表、列、官方字段、样例值、topk、detail 和消歧信息。
3. 基于 `fk` / `rel` / `overlap` / `disambig` 判断 JOIN 路径。
4. 用 `query` 验证值域、分布、空值、计算口径和候选 SQL 结果。
5. 根据问题和 evidence 规划输出列、所需表、JOIN 条件、WHERE 条件、聚合粒度、排序和 LIMIT。

### 输出前检查

1. SQL 中涉及的关键实体已经通过元数据理解。
2. SELECT 和过滤所用字段的含义、值域、代码和口径已经确认。
3. JOIN 路径与读取到的关系实体一致。
4. SELECT、WHERE、GROUP BY、ORDER BY、DISTINCT、LIMIT 都来自问题或 evidence 的输出契约。
5. SQL 能在当前 schema 下执行，并以题目要求的粒度返回结果。
"""


def get_sql_rules() -> str:
    return _SQL_RULES
