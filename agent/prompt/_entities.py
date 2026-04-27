"""实体参考层 — 按文件类型组织的实体命名规则和元数据字段。"""

_ENTITIES_PROMPT = r"""以下是各文件类型可能产生的实体类型。不是每种文件都会有所有实体，实际存在哪些实体取决于提取结果，用 glob 查看。

## 关系型数据库文件 (.db / .sqlite / .sqlite3 等)

### 文件节点

ref 就是文件相对路径：`my_data.db`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |
| `table_count` | 表的数量 |
| `view_count` | 视图的数量 |

### 表 .table

```
<数据库>::<表名>.table
例: my_data.db::users.table
```

| 字段 | 说明 |
|---|---|
| `row_count` | 行数 |
| `column_count` | 列数 |
| `primary_key` | 主键列名（如有） |

### 视图 .view

```
<数据库>::<视图名>.view
例: my_data.db::active_users.view
```

### 列 .col

```
<数据库>::<表名>.<列名>.<数据类型>.col
例: my_data.db::users.id.INT.col
例: my_data.db::users.name.TEXT.col
例: my_data.db::users.salary.REAL.col
例: my_data.db::users.created_at.DATETIME.col
```

注意：列名中的特殊字符（空格、括号等）保持原样，如 `frpm.Free Meal Count (K-12).REAL.col`。用 glob 模糊搜索比精确构造更可靠。

| 字段 | 适用 | 说明 |
|---|---|---|
| `cardinality` | 所有列 | 不同值的近似数量 |
| `null_count` | 所有列 | 空值数量 |
| `null_percentage` | 所有列 | 空值比例 |
| `sample` | 所有列 | 采样值列表（约 20 个） |
| `topk` | 所有列 | 高频值列表（含百分比） |
| `min_value` / `max_value` / `mean_value` | 数值列 | 数值范围 |
| `min_length` / `max_length` / `avg_length` | 文本列 | 长度统计 |

### 外键关系 .fk

```
<数据库>::<源表>.<源列>__to__<目标表>.<目标列>.fk
例: my_data.db::orders.user_id__to__users.id.fk
```

| 字段 | 说明 |
|---|---|
| `brief` | 简述关系类型和方向 |
| `detail` | 详细说明（含理由、置信度、发现方式等） |

### 列值重叠 .overlap

```
<数据库>::<表A>.<列A>__to__<表B>.<列B>.overlap
例: my_data.db::schools.School__to__frpm.School Name.overlap
```

由静态计算生成（KMV sketch 近似），可能遗漏真实的列关联，仅供参考。

| 字段 | 说明 |
|---|---|
| `brief` | 简述关系类型和方向 |
| `detail` | 详细说明（含理由、置信度、发现方式等） |
| `stats` | 统计信息：jaccard / card_overlap / coverage 等 |

### 逻辑关系 .rel

```
<数据库>::<表A>.<列A>__to__<表B>.<列B>.rel
例: my_data.db::schools.County__to__satscores.cname.rel
```

由 AI 推断的语义关系，定位信息已编码在 entity name 中，meta 只有 brief 和 detail。

### 语义消歧 .disambig

```
<数据库>::<歧义术语>.disambig
例: my_data.db::points.disambig
例: my_data.db::results.disambig
```

当数据库中存在名称相同或相近但含义不同的实体时，消歧实体记录这些差异。通过边连接到所有涉及的列或表实体。

| 字段 | 说明 |
|---|---|
| `level` | 消歧层级：`column`（列级）或 `table`（表级） |
| `brief` | ≤50字描述歧义 |
| `detail` | 完整列出每个实体中该术语的具体语义差异 |

- **列级消歧**：同一列名出现在多个表中但含义不同（如 `points` 在 results 表是单场积分，在 standings 表是赛季累计积分）
- **表级消歧**：名称相近的表服务不同场景（如 `results` vs `constructorResults`）

---

## CSV / TSV 文件

### 文件节点

ref 就是文件相对路径：`database_description/schools.csv`

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

ref 就是文件相对路径：`config/settings.json`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |

### JSON 路径模式 .pattern

```
<JSON文件>::<JSON路径>.pattern
例: config/settings.json::$.users.[v].pattern
```

JSON/YAML 文件的结构探查结果。`.pattern` 实体描述了文件中重复出现的结构模式：
- ARRAY 模式：如 `$.records.[v]` 表示 records 数组中每个元素的结构
- DICT 模式：如 `$.config.database` 表示嵌套字典的键值结构

| 字段 | 说明 |
|---|---|
| `name` | JSON 路径，如 `$.users.[v]` |
| `type` | 模式类型：DICT 或 ARRAY |
| `pattern` | 结构描述，如 `each element: {id: INT, name: STR, ...}` |

---

## 文本文件

### 文件节点

ref 就是文件相对路径：`README.md`

| 字段 | 说明 |
|---|---|
| `path` | 文件在项目中的相对路径 |
| `file_size` | 文件大小（字节） |

### 文本分片 .chunk

```
<文本文件>::chunk_<编号>.chunk
例: README.md::chunk_001.chunk
```

长文档的分段，用于超出读取限制的大文件。
"""


def get_entities_prompt() -> str:
    return _ENTITIES_PROMPT
