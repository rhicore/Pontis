"""Shared writing contract for database table and column descriptions."""


DESCRIPTION_CONTRACT = """\
## Description 契约

`brief/detail` 是数据库业务词典：

- `brief` 是不超过 50 字的业务名词短语。
- 表的 `detail` 定义表的用途、业务范围和每行代表的对象或事件。
- 列的 `detail` 定义列的业务含义，以及理解该含义所需的格式、单位或枚举解释。
- description 记录跨数据刷新仍成立的定义。行数、cardinality、null、sample、topk、最值和当前分布由统计 metadata 提供；成员、归属和 JOIN 关系由图边及 `fk/rel/disambig` 实体提供。
- official 字段标记 `unuseful`、`not useful`、`unused`、`ignore` 或同类含义时，brief 和 detail 都写为 `官方标记为不可用`。

数据库原始表名、列名、格式、单位、枚举值和代码值保持原样；说明使用中文。
"""
