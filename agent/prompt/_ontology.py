"""本体层 — 标签类型、连接模式、命名逻辑。"""


def get_ontology_prompt() -> str:
    return r"""## 标签类型

| 标签 | 含义 | 典型实体名 |
|---|---|---|
| `file` | 文件 | `formula_1.db` |
| `db` | 数据库文件 | `formula_1.db` |
| `csv` | CSV 文件 | `schools.csv` |
| `json` | JSON 文件 | `config.json` |
| `text` | 文本文件 | `README.md` |
| `directory` | 目录 | `data` |
| `table` | 数据库表 | `drivers` |
| `view` | 数据库视图 | `active_users` |
| `col` | 数据库列 | `driverId` |
| `INT` / `TEXT` / `REAL` / `BLOB` | 数据类型 | — |
| `fk` | 外键关系 | `orders.user_id->users.id` |
| `rel` | 逻辑关系（AI 推断） | `schools.County->satscores.cname` |
| `overlap` | 列值重叠 | `a.col1->b.col2` |
| `disambig` | 语义消歧 | `points` |
| `convention` | SQL 约定 | `no_concat` |
| `pattern` | SQL 模式 | `ranking_top_n` |
| `term` | 领域术语 | `fiscal_year` |
| `lesson` | 经验教训 | `join_cardinality` |
| `example` | Few-shot 示例 | `top_n_ranking` |

---

## 邻接关系

| 实体类型 | 连接的实体 |
|---|---|
| `directory` | 目录的文件和子目录节点 |
| `db`, `sqlite`等数据库节点 | 数据库里的表和视图,`table`, `view` |
| `table` | 与该表相关的 `col`, `fk`, `rel`, `overlap`等实体 |
| `fk` / `rel` / `overlap` | 连接两张表和对应的两个列, rel在多对多关系中可能连接多个表和多个列 |
| `disambig` / `convention` / `pattern` / `term` / `lesson` / `example` | 这些知识或消歧实体会连接2个或多个与其相关的实体|

---

## 命名逻辑

- **普通实体**：裸名，如 `drivers`、`driverId`、`formula_1.db`
- fk/rel/overlap ：如果数据库中有人工命名，优先采用人工命名，否则会采用`table1.col1->table2.col2`，用 `->` 连接源和目标，类型通过标签区分
- **知识实体**（convention/pattern/term/lesson/example）：简短英文语义标识，如 `no_concat`、`ranking_top_n`
- **消歧实体**（disambig）：歧义词或语义不清的概念
"""
