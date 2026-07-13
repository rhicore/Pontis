"""本体层 — 描述项目实体类型、数据源类型与连接拓扑。"""


def get_database_ontology_prompt() -> str:
    """适用于以数据库节点为 source 的紧凑 ontology。"""
    return r"""## 数据库图谱 Ontology

当前 project 以唯一的 `db` 节点作为 source 和导航起点。数据库结构拓扑是：

```text
db
└── table / view
    └── col
```

数据库语义通过普通图实体和边补充：

| 标签 | 含义 | 典型邻接 |
|---|---|---|
| `db` | 当前数据库 | `table`、`view`、数据库级 `knowledge` |
| `table` / `view` | 物理表或视图 | 所属 `db`、自身 `col`、相关关系实体 |
| `col` | 表或视图中的物理列 | 所属 `table/view`、相关关系或消歧实体 |
| `fk` | 数据库声明的外键关系 | 参与关系的表和列 |
| `rel` | 根据 schema 或数据证据确认的语义关系 | 参与关系的表和列 |
| `overlap` | 多列之间的值域重叠证据 | 具有重叠值域的列及其表 |
| `disambig` | 同名、近义或容易混淆实体之间的语义区分 | 被区分的表、列或其他实体 |
| `knowledge` | 数据库级术语、约定或说明 | 所解释的数据库实体 |

实体身份由 `name:tag` 表示，source-rooted ref 是从 `db` 节点沿实际图边得到的导航坐标。成员、归属和关系端点由边表达；所有实体使用同一套 `find`、`meta` 和邻接读取方式。
"""


def get_ontology_prompt() -> str:
    return r"""## 数据源与实体类型参考

当前最重要的数据源类型是：

### `fs`
一个 `fs` project 的 source 是一个真实目录，图通常从目录树开始展开。

它的基础部分通常由下面几类实体组成：

- 根目录节点 `.:dir`；当 project 的 source type 是 `fs` 时，它是唯一导航起点
- 子目录节点 `*:dir`
- 文件节点 `*:file`
- 如果文件是数据库，则出现 `file:db`，与其连接的实体请看`db`部分
- 如果文件是 CSV / TSV，则出现 `file:csv`
- 如果文件是 JSON / YAML / XML / TOML / HCL，则出现对应的序列化文件节点
- 如果文件是普通文本，则通常带 `text`
- 在这些 source 节点之上，再叠加提取出的持久知识节点


### `db`

如果某些场景里 project 直接以数据库连接作为 source，而不是“目录里有一个数据库文件”，那么入口节点就不一定先经过 `dir -> file:db`，而可能直接从数据库实体开始。

数据库的实体连接情况是：
- 数据库本身是一个 `db` 节点；只有直连数据库 project 才以它作为唯一导航起点，fs project 中的数据库仍从根目录进入
- 与数据库节点相连的是 `table/view`
- `table/view` 下面是 `col`
- `fk/rel/overlap/disambig` 围绕表和列展开

## 实体标签类型

下面是当前设计里最重要的实体类型。不是每个 project 都会同时拥有全部类型，但这些都是系统允许存在的核心标签。

### 文件系统 / 对象层

| 标签 | 含义 | 典型例子 | 邻接实体 | 实体命名方式 | 常见元数据 |
|---|---|---|---|---|---|
| `dir` | 目录节点或前缀层级节点 | `.`, `data`, `tables` | 通常连接父目录、子目录，以及目录下的各种 `file` 节点 | 通常直接用相对路径或目录名命名，例如 `.`、`data/raw` | `path`, `file_count`, `subdir_count`, `brief`, `detail` |
| `file` | 普通文件节点基标签 | `formula_1.sqlite`, `schools.csv`, `README.md` | 通常连接所在的 `dir`，他作为特定类型文件可以连接其他类型节点 | 通常直接用文件名或相对路径命名，例如 `formula_1.sqlite`、`docs/README.md` | `path`, `file_size`, `line_count`, `char_count`, `brief`, `detail` |
| `db` | 数据库文件或数据库入口 | `formula_1.sqlite` | 通常连接数据库中的 `table`、`view`，并间接成为 `col`、`fk`、`rel`、`disambig` 的上游入口 | 如果来自文件系统，通常直接用数据库文件名命名；如果来自直连 source，则通常用数据库名或逻辑库名命名 | `index_count`, `file_size`, `brief`, `detail` |
| `csv` | CSV / TSV 文件 | `schools.csv` | 通常连接所在目录；默认不把表头投影为图中的表列实体 | 通常直接用 CSV/TSV 文件名或相对路径命名 | `line_count`, `char_count`, `brief`, `detail` |
| `json`/`yaml`/`xml` 等| 序列化文件节点 | `config.json`, `config.yaml`, `export.xml` | 通常连接所在 `dir` | 通常直接用文件名或相对路径命名 | `structure_type`, `key_count`, `top_level_keys`, `line_count`, `char_count`, `brief`, `detail` |
| `text` | 普通文本文件 | `notes.txt`, `README.md` | 通常连接所在 `dir`，也可能直接连接被它解释的 `file`、`table`、`col` | 通常直接用文件名或相对路径命名，说明性文件常见名字如 `README.md`、`notes.txt` | `line_count`, `char_count`, `brief`, `detail` |

### 数据库 / 结构层

| 标签 | 含义 | 典型例子 | 邻接实体 | 实体命名方式 | 常见元数据 |
|---|---|---|---|---|---|
| `schema` | 数据库官方 namespace，类似 Snowflake schema/命名空间 | `PUBLIC`, `FEC` | 通常连接上游 `db`，以及 namespace 内的 `table/view`；在大型数据库中也可连接导航层的 `table_group/topic` | 通常用官方 schema 名，内部 ref 常为 `<db>--<schema>` | `name`, `table_count`, `view_count`, `brief`, `detail` |
| `table` | 数据库表 | `drivers` | 通常连接上游的 `db`，连接自己的 `col`，也常连接 `fk`、`rel`、`overlap`、`disambig` | 通常直接用数据库里的表名命名，例如 `drivers`、`schools` | `row_count`, `primary_key`, `brief`, `detail` |
| `view` | 数据库视图 | `active_users` | 通常连接上游的 `db`，连接自己的 `col`，也可能连接 `rel`、`disambig` | 通常直接用数据库里的视图名命名，例如 `active_users` | `row_count`, `brief`, `detail` |
| `col` | 数据库表列或视图列 | `driverId`, `school_name` | 通常连接所属的 `table` 或 `view`，也常连接 `fk`、`rel`、`overlap`、`disambig` | 通常直接用列名命名；图谱 ref 通过 `/` 沿相邻节点路径访问 | `official_column_description`, `official_value_description`, `cardinality`, `null_count`, `null_percentage`, `sample`, `topk`, `min_value`, `max_value`, `mean_value`, `min_length`, `max_length`, `avg_length`, `domain_profile` (`physical_family`, `value_format`, `integer_bits`, `integer_has_negative`, `numeric_min`, `numeric_max`), `domain_profile_method`, `brief`, `detail` |
| `fk` | 数据库中显式建立的外键| `orders.user_id->users.id` | 通常连接所属 `db`，同时连接源 `table`、源 `col`、目标 `table`、目标 `col` | 如果没有人工命名，通常按 `源表.源列->目标表.目标列` 的形式命名 | `detail`, `match_rate`, `format_hint`, `brief` |
| `INT` / `TEXT` / `REAL` / `BLOB` / `BOOL` / `DATETIME` / `JSON` / `FLOAT` | 列类型标签 | `driverId:INT:col` | 这些标签通常附着在 `col` 上，用来说明列的类型；带这些标签的实体最常连接所属的 `table/view`，以及 `fk`、`rel`、`overlap` 等列级关系实体 | 这些通常不是独立命名的实体，而是作为附着在 `col` 上的类型标签出现，例如 `driverId:INT:col` | 使用 `col` 实体元数据 |

### 大型数据库导航层

大型数据库可能有几百张物理表和几万列。先读取导航层，再按命中的主题或分组展开相关 `table/col`。

| 标签 | 含义 | 典型例子 | 邻接实体 | 实体命名方式 | 常见元数据 |
|---|---|---|---|---|---|
| `table_group` | 命名模式显示为同一逻辑表的物理分片/版本/时间分区 | `GA_SESSIONS_YYYYMMDD`, `INDIVYY` | 通常连接所属 `db/schema` 和成员 `table` | `<db>--table_group--<schema>--<family>` | `family`, `member_count`, `representative_members`, `common_columns`, `variable_columns`, `consistency`, `cognitive_shape`, `agent_usage_hint` |
| `logical_col` | `table_group` 各成员表中同一列角色的逻辑列，例如多个年份分片中的 `patent_id` | `logical_col[patent_id]` | 连接一个 `table_group` 和该角色对应的所有物理 `col`；不带 `col` 标签 | `<table_group_ref>--logical_col--<role>--<member_digest>` | `role`, `member_count`, `table_count`, `data_types`, `grouping_method` |
| `value_domain` / `domain` | 多个逻辑列或 standalone 物理列共享的候选值域，由静态 extractor 聚类并等待 agent 审核 | `airport_code`、`arrival_airport`、`departure_airport` 的机场代码域 | 连接所属 `db/schema` 和成员 `logical_col` 或 standalone `col`；它不是外键，也不保证任意成员都可直接 join | `<db>--value_domain--<schema>--<member_digest>` | `member_count`, `union_cardinality`, `semantic_roles`, `overlap_metric`, `overlap_threshold`, `min_anchor_support`, `review_status` (`pending_review` / `accepted` / `needs_split` / `rejected`), `extraction_evidence`, `brief`, `detail` |
| `topic` | agent 创建的 schema 内语义主题分组，不属于官方 schema 结构 | `clinical`, `campaign_finance_transactions`, `patent_citations` | 通常连接所属 `schema`，以及相关 `table_group` 和 standalone `table` | `<db>.<schema>.<topic_key>:topic:knowledge` | `topic_key`, `topic_label`, `brief`, `detail`, `grouping_method`, `source`, `scope`, `confidence`, `agent_usage_hint` |

读取顺序：先读 `schema_landscape.detail`，再看 `schema/topic`，然后展开命中的 `table_group` 或 standalone `table`。`table_group.consistency=same_order/same_set` 时，代表表通常足以理解全组；`drifting` 时把 `common_columns` 作为稳定 schema，命中具体成员后再看 `variable_columns`。`grouped/standalone` 是实体局部导航标签：`table:grouped` 表示该表被 `table_group` 覆盖；查询时与主实体标签一起使用，例如 `table:standalone`。

### 语义关系层

| 标签 | 含义 | 典型例子 | 邻接实体 | 实体命名方式 | 常见元数据 |
|---|---|---|---|---|---|
| `rel` | agent / extractor 推断出的行级对齐关系 | `schools.County->satscores.cname` | 通常连接两个或多个相关的 `table`、`view`、`col`，用于记录行级匹配依据、匹配率和边界 | 如果没有人工命名，通常也按 `实体A->实体B` 的关系摘要方式命名，常见为 `表.列->表.列` | `brief`, `detail`, `stats`, `match_rate`, `format_hint` |
| `overlap` | 列重叠关系| `a.col1->b.col2` | 通常连接两侧的 `col`，和两侧所属的 `table`，用于提示值域重叠 | 通常按 `表.列->表.列` 这种列对形式命名，强调两侧发生重叠的列 | `brief`, `detail`, `stats` |
| `disambig` | 消歧节点，用于提醒同名/近义但不同语义的实体 | `points`, `rating`, `status` | 通常连接两个或多个容易混淆的 `table`、`view`、`col` | 通常直接用歧义词、歧义短语或容易混淆的概念名命名，例如 `points`、`status` | `brief`, `detail` |


"""
