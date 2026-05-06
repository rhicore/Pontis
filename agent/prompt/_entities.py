"""实体参考层 — 按文件类型组织的实体命名规则和元数据字段。"""

_ENTITIES_PROMPT = r"""以下是各文件类型可能产生的实体类型。不是每种文件都会有所有实体，实际存在哪些实体取决于提取结果，用 glob 查看。

## 目录

目录是虚节点（不持久化），由系统自动发现。

entity_name：裸目录名（如 `data`、`docs`），labels：`["dir"]`
根目录 entity_name 为 `.`。

用图遍历查询目录内容：`glob "(data)--(*:file)"` 看 data 目录下的文件。

| 字段 | 说明 |
|---|---|
| `child_count` | 子项总数 |
| `file_count` | 直接子文件数 |
| `subdir_count` | 直接子目录数 |

---

## 关系型数据库文件 (.db / .sqlite / .sqlite3 等)

### 文件节点

entity_name 就是文件相对路径：`my_data.db`，labels：`["file/db"]`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |
| `table_count` | 表的数量 |
| `view_count` | 视图的数量 |

### 表

entity_name：`<表名>`（裸名），labels：`["table"]`，例：`users`
通过边连接到所属数据库文件和列实体。

| 字段 | 说明 |
|---|---|
| `row_count` | 行数 |
| `column_count` | 列数 |
| `primary_key` | 主键列名（如有） |

### 视图

entity_name：`<视图名>`（裸名），labels：`["view"]`，例：`active_users`

### 列

entity_name：`<列名>`（裸名），labels：`["col/<类型>"]`，例：`driverId`（labels: `["col/INT"]`）

类型在 labels 中：`col/INT`、`col/TEXT`、`col/REAL`、`col/BLOB` 等。

注意：不同表的列可能同名（如多张表都有 `id` 列），用 meta 查看邻接关系确认归属。

| 字段 | 适用 | 说明 |
|---|---|---|
| `cardinality` | 所有列 | 不同值的近似数量 |
| `null_count` | 所有列 | 空值数量 |
| `null_percentage` | 所有列 | 空值比例 |
| `sample` | 所有列 | 采样值列表（约 20 个） |
| `topk` | 所有列 | 高频值列表（含百分比） |
| `min_value` / `max_value` / `mean_value` | 数值列 | 数值范围 |
| `min_length` / `max_length` / `avg_length` | 文本列 | 长度统计 |

### 外键关系

entity_name：`<源表>.<源列>__to__<目标表>.<目标列>`，labels：`["fk"]`
例：`orders.user_id__to__users.id`

| 字段 | 说明 |
|---|---|
| `brief` | 简述关系类型和方向 |
| `detail` | 详细说明（含理由、置信度、发现方式等） |

### 列值重叠

entity_name：`<表A>.<列A>__to__<表B>.<列B>`，labels：`["overlap"]`
例：`schools.School__to__frpm.School Name`

由静态计算生成（KMV sketch 近似），可能遗漏真实的列关联，仅供参考。

| 字段 | 说明 |
|---|---|
| `brief` | 简述关系类型和方向 |
| `detail` | 详细说明（含理由、置信度、发现方式等） |
| `stats` | 统计信息：jaccard / card_overlap / coverage 等 |

### 逻辑关系

entity_name：`<表A>.<列A>__to__<表B>.<列B>`，labels：`["rel"]`
例：`schools.County__to__satscores.cname`

由 AI 推断的语义关系，定位信息已编码在 entity name 中。
**注意**：.rel 是 AI 推断的辅助线索，可靠性低于 .fk（物理外键）。使用 .rel 作为 JOIN 条件时需要额外验证。

### 语义消歧 .disambig

entity_name：`<歧义术语>.disambig`，例：`points.disambig`、`results.disambig`

当数据库中存在名称相同或相近但含义不同的实体时，消歧实体记录这些差异。

| 字段 | 说明 |
|---|---|
| `level` | 消歧层级：`column`（列级）或 `table`（表级） |
| `brief` | ≤50字描述歧义 |
| `detail` | 完整列出每个实体中该术语的具体语义差异 |

---

## CSV / TSV 文件

### 文件节点

entity_name 就是文件相对路径：`database_description/schools.csv`，labels：`["file/csv"]`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |
| `row_count` | 行数 |
| `column_count` | 列数 |
| `delimiter` | 分隔符 |

---

## JSON 文件

### 文件节点

entity_name 就是文件相对路径：`config/settings.json`，labels：`["file/json"]`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |

### JSON 路径模式 .pattern

entity_name：`<JSON路径>.pattern`，例：`$.users.[v].pattern`

JSON/YAML 文件的结构探查结果。

| 字段 | 说明 |
|---|---|
| `name` | JSON 路径，如 `$.users.[v]` |
| `type` | 模式类型：DICT 或 ARRAY |
| `pattern` | 结构描述 |

---

## 文本文件

### 文件节点

entity_name 就是文件相对路径：`README.md`，labels：`["file/text"]`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |

### 文本分片 .chunk

entity_name：`chunk_<编号>.chunk`，例：`chunk_001.chunk`

---

## 知识实体

知识实体不属于特定文件，labels 含 `knowledge`。使用 `glob "*:knowledge"` 发现。

### SQL 约定 .convention

entity_name：`<约定名>.convention`，labels：`["knowledge/convention"]`，例：`no_concat.convention`

| 字段 | 说明 |
|---|---|
| `content` | 约定内容 |

### SQL 模式 .pattern

entity_name：`<模式名>.pattern`，例：`ranking_top_n.pattern`

### 领域术语 .term

entity_name：`<术语名>.term`，例：`points.term`

### Few-shot 示例 .example

entity_name：`<示例名>.example`，例：`top_n_ranking.example`
"""


def get_entities_prompt() -> str:
    return _ENTITIES_PROMPT
