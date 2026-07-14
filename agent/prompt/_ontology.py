"""Ontology prompts for database-backed Pontis projects."""


_DATABASE_ONTOLOGY = r"""## 数据库图谱 Ontology

当前 project 只有一个 source：`db` 节点。它也是图导航的唯一根节点。

```text
db
├── table / view
│   └── col
└── knowledge
```

除物理结构外，图中还可能包含以下普通实体：

| 标签 | 含义 |
|---|---|
| `fk` | 数据库声明的外键关系 |
| `rel` | 根据 schema 或数据证据确认的语义关系 |
| `column_domain` | 多列之间的候选共享值域；不等同于可直接 JOIN |
| `disambig` | 同名、近义或易混淆实体之间的语义区分 |
| `knowledge` | 数据库级术语、业务约定或补充说明 |

`table/view` 连接所属 `db` 和自己的 `col`；关系与语义实体通过边连接其涉及的表、列或数据库。成员、归属和关系端点均以边表达，不应依赖重复 metadata。

所有实体使用相同的工具语义和输出格式：`find` 返回从唯一 `db` source 回溯得到的 `name:tag` ref，`meta` 读取实体自身信息及邻接入口，再沿邻接实体继续探索；`fk`、`rel`、`column_domain` 与其他实体同等处理。
"""


_LARGE_DATABASE_EXTENSION = r"""## 大型数据库导航扩展

大型数据库还可能使用以下导航实体。它们用于缩小探索范围，不取代物理 `table/col`：

| 标签 | 含义 |
|---|---|
| `schema` | 数据库官方 namespace，连接其中的 `table/view` |
| `table_group` | 同一逻辑表的物理分片、版本或时间分区集合 |
| `logical_col` | `table_group` 成员表中承担同一角色的物理列集合；它不是物理 `col` |
| `topic` | agent 创建的语义主题，连接相关 `table_group` 或独立表 |

优先从 `schema/topic` 缩小范围，再展开命中的 `table_group` 或独立 `table`。`table_group` 只能帮助导航；生成 SQL 前仍需确认实际物理表和列。共享 `column_domain` 只提供候选语义证据，不能单独证明 JOIN 正确。
"""


def get_database_ontology_prompt() -> str:
    """Return the compact ontology used by SQLite/PostgreSQL projects."""
    return _DATABASE_ONTOLOGY


def get_ontology_prompt() -> str:
    """Return the database ontology with large-schema navigation concepts."""
    return f"{_DATABASE_ONTOLOGY}\n\n{_LARGE_DATABASE_EXTENSION}"
