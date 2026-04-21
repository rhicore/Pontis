# Pontis 实体 Meta 字段参考

设计原则：
- **名字已包含的信息不重复存储**（如 entity ref 中的表名、列名）
- **不确定/AI 生成的信息写进 brief/detail**，不单独建字段
- **只存从名字和原始数据无法直接推导的结构化信息**

所有实体共享的可写字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `brief` | str | 简短描述（≤50字） |
| `detail` | str | 详细描述 |
| `created_at` | str (ISO) | 节点创建时间 |

---

## 数据库相关

### `.db` / `.sqlite` / `.duckdb` — 数据库文件

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `path` | str | db_basic | 相对路径 |
| `table_count` | int | db_info | 表数量 |
| `view_count` | int | db_info | 视图数量 |
| `index_count` | int | db_info | 索引数量 |
| `file_size` | int | enricher | 文件大小（字节） |
| `modified_at` | str | db_basic | 文件修改时间 |

### `.table` — 数据库表

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `row_count` | int | db_table_info | 行数 |
| `column_count` | int | db_table_info | 列数 |
| `primary_key` | str? | db_table_info | 主键列名 |
| `col` | str | enricher | 子列 ref 列表（虚拟） |

### `.view` — 数据库视图

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `row_count` | int | enricher | 行数 |
| `column_count` | int | enricher | 列数 |
| `col` | str | enricher | 子列 ref 列表（虚拟） |

### `.col` — 列（数据库 / CSV）

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `cardinality` | int | sketch_stats | 不同值数量 |
| `null_count` | int | sketch_stats | 空值数量 |
| `null_percentage` | float | sketch_stats | 空值比例 |
| `min_value` | numeric? | sketch_stats | 最小值（数值列） |
| `max_value` | numeric? | sketch_stats | 最大值（数值列） |
| `mean_value` | float? | sketch_stats | 平均值（数值列） |
| `min_length` | int? | sketch_stats | 最小长度（文本列） |
| `max_length` | int? | sketch_stats | 最大长度（文本列） |
| `avg_length` | float? | sketch_stats | 平均长度（文本列） |
| `sample` | list | sketch_stats | 样本值 |
| `topk` | list[dict] | sketch_stats | 高频值 `[{value, count, percentage}]` |
| `_index` | dict | lsh_index | LSH 布隆过滤器索引（内部字段，对 agent 不可见） |

注：CSV 列用 `min`/`max`/`mean`（非 `min_value`/`max_value`/`mean_value`）。

### `.fk` — 外键关系

名字格式 `{from_table}.{from_column}__to__{to_table}.{to_column}.fk` 已包含全部定位信息。

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `brief` | str | table_relations / agent | 简述（如 "atom.molecule_id → molecule.molecule_id 外键"） |
| `detail` | str | table_relations / agent | 详细说明（含置信度、发现方式等） |

### `.overlap` — 列值重叠

名字格式 `{from_table}.{from_column}__to__{to_table}.{to_column}.overlap` 已包含定位信息。

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `stats` | dict | sketch_overlap | 统计信息（见下表） |
| `brief` | str | agent | 简述 |
| `detail` | str | agent | 详细说明（含匹配类型、理由等） |

`stats` 子字段：

| 字段 | 说明 |
|---|---|
| `card_overlap` | 估计交集大小 |
| `jaccard` | Jaccard 相似度 |
| `cardinality_A` | 源列不同值数 |
| `cardinality_B` | 目标列不同值数 |
| `coverage_A_in_B` | 源列覆盖率 |
| `coverage_B_in_A` | 目标列覆盖率 |

### `.rel` — 列关系（Agent / AI 创建）

名字格式 `{from_table}.{from_column}__rel__{to_table}.{to_column}.rel` 已包含全部定位信息。

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `brief` | str | agent | 简述（含关系类型） |
| `detail` | str | agent | 详细说明（含发现方式、理由等） |

---

## 文件相关

### `.csv` / `.tsv` — CSV/TSV 文件

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `path` | str | csv_basic | 相对路径 |
| `delimiter` | str | csv_basic | 分隔符 |
| `row_count` | int | csv_info | 数据行数 |
| `column_count` | int | csv_info | 列数 |
| `file_size` | int | enricher | 文件大小 |
| `modified_at` | str | csv_basic | 文件修改时间 |

### `.json` — JSON 文件

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `path` | str | serialized_basic | 相对路径 |
| `file_size` | int | serialized_basic | 文件大小 |
| `line_count` | int | serialized_basic | 行数 |
| `char_count` | int | serialized_basic | 字符数 |
| `structure_type` | str | serialized_basic | `"object"` / `"array"` 等 |
| `top_level_keys` | list[str] | serialized_basic | 顶层键（前20个） |
| `key_count` | int | serialized_basic | 顶层键数量 |
| `array_length` | int | serialized_basic | 数组长度（根为数组时） |
| `_index` | dict | lsh_index | LSH 索引（内部字段，对 agent 不可见） |

### `.yaml` / `.yml` — YAML 文件

与 JSON 类似，额外：

| 字段 | 类型 | 说明 |
|---|---|---|
| `sequence_length` | int | 序列长度（替代 `array_length`） |

### `.md` / `.txt` — 文本文件

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `path` | str | text_basic | 相对路径 |
| `file_size` | int | enricher | 文件大小 |
| `encoding` | str | text_info | 文件编码 |
| `char_count` | int | text_info | 字符数 |
| `line_count` | int | text_info | 行数 |
| `empty_line_count` | int | text_info | 空行数 |
| `non_empty_line_count` | int | text_info | 非空行数 |
| `avg_line_length` | float | text_info | 平均行长度 |
| `max_line_length` | int | text_info | 最大行长度 |
| `modified_at` | str | text_basic | 文件修改时间 |

---

## 其他

### `.pattern` — JSON 模式

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | str | JSON 路径名 |
| `type` | str | `"DICT"` 或 `"ARRAY"` |
| `pattern` | str | 模式描述 |
| `ai_summary` | str | AI 总结 |

### 目录节点（无后缀）

| 字段 | 类型 | 说明 |
|---|---|---|
| `child_count` | int | 子项总数 |
| `file_count` | int | 子文件数 |
| `subdir_count` | int | 子目录数 |
