"""Ontology prompts for database-backed Pontis projects."""


_DATABASE_ONTOLOGY = r"""## 数据库图谱 Ontology

当前 project 只有一个 source：`db` 节点。它也是图导航的唯一根节点。

```text
db
├── table / view ── col
├── fk / rel / disambig
│   └── 涉及的 table / view / col
└── knowledge ── 被说明的数据库实体
```

核心实体及其连接方式：

| 标签 | 含义 | 直接连接的实体 |
|---|---|---|
| `db` | 当前数据库，也是唯一 source 和导航根 | 自身的 `table/view`，数据库级关系与 `knowledge` |
| `table` | 数据库中的物理表 | 所属 `db`、自身 `col`、涉及该表的关系或语义实体 |
| `view` | 数据库中的物理视图 | 所属 `db`、自身 `col`、涉及该视图的关系或语义实体 |
| `col` | 表或视图中的物理列 | 所属 `table/view`、涉及该列的 `fk/rel/disambig` |
| `fk` | 数据库声明的外键关系 | 参与外键的表和列 |
| `rel` | 根据 schema 或数据证据确认的语义关系 | 参与关系的表、视图和列 |
| `disambig` | 同名、近义或易混淆实体之间的语义区分 | 被区分的表、视图、列或其他实体 |
| `knowledge` | 数据库术语、业务约定或补充说明 | 所属 `db` 或它所解释的实体 |

边表达实体之间的归属、成员和关系端点。`db -> table/view -> col` 是物理结构主线；从表或列可继续进入相邻的关系、消歧和知识实体，再从这些实体查看其他参与者。FK 是无方向的关系实体，它的名称和成员足以表达外键事实。

列的稳定展示坐标使用物理归属路径 `db/table-or-view/col`。关系实体可能同时连接多个表列，但它们与成员实体之间是普通邻接，不会改变列的结构坐标。能够由边读取的成员、所属表和端点不在 metadata 中重复保存。

所有实体使用相同的工具语义和输出格式：`find` 返回从唯一 `db` source 回溯得到的 `name:tag` ref，`meta` 读取实体自身信息及邻接入口，再沿邻接实体继续探索；`fk`、`rel`、`disambig` 与其他实体同等处理。
"""


_LARGE_DATABASE_EXTENSION = r"""## 大型数据库导航扩展

大型数据库还可能使用以下导航实体。它们用于缩小探索范围，不取代物理 `table/col`：

| 标签 | 含义 |
|---|---|
| `schema` | 数据库官方 namespace，连接其中的 `table/view` |
| `table_group` | 同一逻辑表的物理分片、版本或时间分区集合 |
| `logical_col` | `table_group` 成员表中承担同一角色的物理列集合；它不是物理 `col` |
| `topic` | agent 创建的语义主题，连接相关 `table_group` 或独立表 |

优先从 `schema/topic` 缩小范围，再展开命中的 `table_group` 或独立 `table`。`table_group` 只能帮助导航；生成 SQL 前仍需确认实际物理表和列，并通过 `fk/rel/disambig` 理解连接和字段选择。
"""


def get_database_ontology_prompt() -> str:
    """Return the compact ontology used by SQLite/PostgreSQL projects."""
    return _DATABASE_ONTOLOGY


def get_ontology_prompt() -> str:
    """Return the database ontology with large-schema navigation concepts."""
    return f"{_DATABASE_ONTOLOGY}\n\n{_LARGE_DATABASE_EXTENSION}"
