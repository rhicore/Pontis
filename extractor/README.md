# Extractor 元数据字段参考

每个 extractor 模块生成的元数据字段一览。元数据存储在 `.pontis/` 目录下的 `_meta.yml` 中。

---

## 1. 数据库文件 `.db`

### 文件级 `_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `modified_at` | skeleton | 文件修改时间 (ISO 8601) |
| `created_at` | skeleton | VFS 节点创建时间 |
| `table_count` | db_info | 表数量 |
| `view_count` | db_info | 视图数量 |
| `index_count` | db_info | 索引数量 |
| `file_size` | db_info | 文件大小 (字节) |
| `brief` | ai_db_table_summary | AI 一句话概括 (<50 字符) |
| `detail` | ai_db_table_summary | AI 详细总结 |

### 表实体 `_entity/{table}.table/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `created_at` | db_basic | 创建时间 |
| `row_count` | db_table_info | 行数 |
| `column_count` | db_table_info | 列数 |
| `primary_key` | db_table_info | 主键列名 |
| `brief` | ai_db_table_summary | AI 一句话概括 (<50 字符) |
| `detail` | ai_db_table_summary | AI 详细总结 |

### 列实体 `_entity/{table}.{col}.{type}.col/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `created_at` | db_basic | 创建时间 |
| `source_table` | db_basic | 所属表名 |
| `cardinality` | db_column_stats | 非空去重值数量 |
| `null_count` | db_column_stats | NULL 值数量 |
| `null_percentage` | db_column_stats | NULL 比例 (保留 2 位小数) |
| `min_value` | db_column_stats | 最小值 (仅数值列) |
| `max_value` | db_column_stats | 最大值 (仅数值列) |
| `mean_value` | db_column_stats | 平均值 (仅数值列) |
| `min_length` | db_column_stats | 最短字符串长度 (仅文本列) |
| `max_length` | db_column_stats | 最长字符串长度 (仅文本列) |
| `avg_length` | db_column_stats | 平均字符串长度 (仅文本列) |
| `sample` | db_column_sample | 采样值列表 (最多 10 个去重值) |
| `topk` | db_column_topk | 高频值列表，每项 `{value, count, percentage}` (默认前 5) |
| `brief` | ai_db_column_summary | AI 一句话概括 (<50 字符) |
| `detail` | ai_db_column_summary | AI 详细总结 |

### 外键关系 `_entity/{from}__to__{to}.fk/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `relation_type` | db_table_relations | `"foreign_key"` 或 `"naming_convention"` |
| `from_table` | db_table_relations | 源表 |
| `from_column` | db_table_relations | 源列 |
| `to_table` | db_table_relations | 目标表 |
| `to_column` | db_table_relations | 目标列 |
| `confidence` | db_table_relations | 置信度 (FK=1.0, 命名约定=0.7) |
| `created_at` | db_table_relations | 创建时间 |

### 列重叠 `_entity/{from}__to__{to}.overlap/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `relation_type` | db_column_overlap | `"column_overlap"` |
| `from_table` | db_column_overlap | 源表 |
| `from_column` | db_column_overlap | 源列 |
| `to_table` | db_column_overlap | 目标表 |
| `to_column` | db_column_overlap | 目标列 |
| `match_type` | db_column_overlap | `"STRONG_MATCH"` 或 `"WEAK_MATCH"` |
| `reason` | db_column_overlap | 匹配原因说明 |
| `stats.jaccard` | db_column_overlap | Jaccard 相似系数 |
| `stats.card_overlap` | db_column_overlap | 重叠去重值数量 |
| `created_at` | db_column_overlap | 创建时间 |

### 列关系 `_entity/{from}__to__{to}.rel/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `relation_type` | db_column_rel | `"column_relation"` |
| `from_table` | db_column_rel | 源表 |
| `from_column` | db_column_rel | 源列 |
| `to_table` | db_column_rel | 目标表 |
| `to_column` | db_column_rel | 目标列 |
| `confidence` | db_column_rel | LLM 置信度 (0.0-1.0) |
| `can_join` | db_column_rel | LLM 判断是否可 JOIN |
| `reason` | db_column_rel | LLM 给出的解释 |
| `created_at` | db_column_rel | 创建时间 |

---

## 2. CSV/TSV 文件 `.csv` `.tsv`

### 文件级 `_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `modified_at` | skeleton | 文件修改时间 |
| `created_at` | skeleton | VFS 节点创建时间 |
| `row_count` | csv_info | 数据行数 (不含表头) |
| `column_count` | csv_info | 列数 |
| `file_size` | csv_info | 文件大小 (字节) |
| `delimiter` | csv_info | 分隔符 (`","` 或 `"\t"`) |

### 列实体 `_entity/{table}.{col}.TEXT.col/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `created_at` | csv_basic | 创建时间 |
| `cardinality` | csv_column_stats | 去重非空值数量 |
| `null_count` | csv_column_stats | 空/NULL 值数量 |
| `null_percentage` | csv_column_stats | 空值比例 |
| `min` | csv_column_stats | 最小数值 (仅数值列) |
| `max` | csv_column_stats | 最大数值 (仅数值列) |
| `mean` | csv_column_stats | 平均数值 (仅数值列) |
| `sample` | csv_column_sample | 采样值列表 (最多 10 个) |
| `topk` | csv_column_topk | 高频值列表 `{value, count, percentage}` (前 5) |

---

## 3. JSON 文件 `.json`

### 文件级 `_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `modified_at` | skeleton | 文件修改时间 |
| `created_at` | skeleton | VFS 节点创建时间 |
| `file_size` | serialized_basic | 文件大小 (字节) |
| `line_count` | serialized_basic | 行数 |
| `char_count` | serialized_basic | 字符数 |
| `structure_type` | serialized_basic | 顶层结构: `"object"` / `"array"` 等 |
| `top_level_keys` | serialized_basic | 顶层 key 列表 (前 20 个) |
| `key_count` | serialized_basic | 顶层 key 数量 |
| `array_length` | serialized_basic | 数组元素数量 (仅 array 类型) |
| `brief` | ai_json_summary | AI 一句话概括 (<50 字符) |
| `detail` | ai_json_summary | AI 详细总结 |

### 模式实体 `_entity/{pattern}.pattern/_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `name` | json_pattern | JSONPath 路径 |
| `pattern` | json_pattern | 人类可读的模式描述 |

---

## 4. YAML 文件 `.yaml` `.yml`

### 文件级 `_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `modified_at` | skeleton | 文件修改时间 |
| `created_at` | skeleton | VFS 节点创建时间 |
| `file_size` | serialized_basic | 文件大小 (字节) |
| `line_count` | serialized_basic | 行数 |
| `char_count` | serialized_basic | 字符数 |
| `structure_type` | serialized_basic | 顶层结构: `"mapping"` / `"sequence"` 等 |
| `top_level_keys` | serialized_basic | 顶层 key 列表 |
| `key_count` | serialized_basic | 顶层 key 数量 |
| `sequence_length` | serialized_basic | 序列元素数量 (仅 sequence 类型) |

---

## 5. 文本文件 `.md` `.txt` `.log` `.sql`

### 文件级 `_meta.yml`

| 字段 | 来源模块 | 说明 |
|------|----------|------|
| `modified_at` | skeleton | 文件修改时间 |
| `created_at` | skeleton | VFS 节点创建时间 |
| `file_size` | text_info | 文件大小 (字节) |
| `encoding` | text_info | 检测到的编码 |
| `char_count` | text_info | 字符数 |
| `line_count` | text_info | 行数 |
| `empty_line_count` | text_info | 空行数 |
| `non_empty_line_count` | text_info | 非空行数 |
| `avg_line_length` | text_info | 平均行长度 |
| `max_line_length` | text_info | 最大行长度 |
| `brief` | ai_text_summary | AI 一句话概括 (<50 字符) |
| `detail` | ai_text_summary | AI 详细总结 |
