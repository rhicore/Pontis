"""Shared writing contract for database table and column descriptions."""


DESCRIPTION_CONTRACT = """\
## Description 契约

`brief/detail` 是数据库业务词典：

- `brief` 是不超过 50 字的业务名词短语。
- 表的 `detail` 定义表的用途、业务范围和每行代表的对象或事件。
- 列的 `detail` 定义列的业务含义，以及理解该含义所需的格式、单位或枚举解释。
- description 记录跨数据刷新仍成立、只属于当前实体自身的定义。
- 表列归属由结构边表达；主外键和 JOIN 关系由 `fk/rel` 实体及其边表达；候选之间的选择边界由 `disambig` 实体及其边表达。因此 table/col 的 detail 不复述“引用/关联/连接某表某列”，也不罗列其他实体名称。
- 行数、distinct/cardinality、null、sample、topk、最值、当前枚举数量和当前分布由统计 metadata 提供，不写进 description。格式、单位以及枚举值各自代表什么是稳定语义，可以写入。
- official 字段标记 `unuseful`、`not useful`、`unused`、`ignore` 或同类含义时，brief 和 detail 都写为 `官方标记为不可用`。

数据库原始表名、列名、格式、单位、枚举值和代码值保持原样；说明使用中文。
"""
