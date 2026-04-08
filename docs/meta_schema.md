# VFS Meta Schema Reference

## `.db` - Database

```yaml
path: str                    # 原始文件相对路径
modified_at: str             # ISO格式修改时间
created_at: str              # ISO格式创建时间
table_count: int             # 表数量
view_count: int              # 视图数量
index_count: int             # 索引数量
file_size: int               # 文件大小(字节)
```

## `.table` - Database Table

```yaml
created_at: str              # ISO格式创建时间
row_count: int               # 行数
column_count: int            # 列数
primary_key: str|null        # 主键列名
semantic_summary: str|null   # AI生成的语义描述
```

## `.col` - Column (DB/CSV/TSV)

```yaml
# 基础字段 (由skeleton生成)
created_at: str
source_table: str            # DB列: 所属表名
source_view: str             # 视图列: 所属视图名

# 统计字段 (由db_column_stats/csv_column_stats生成)
cardinality: int             # 唯一值数量
null_count: int              # NULL值数量
null_percentage: float       # NULL值百分比

# 数值类型特有
min_value: number|null
max_value: number|null
mean_value: number|null

# 文本类型特有
min_length: int|null
max_length: int|null
avg_length: float|null

# 采样数据 (由db_column_sample/csv_column_sample生成)
sample:                      # 直接是数组,不是嵌套对象
  - value1
  - value2

# TopK数据 (由db_column_topk/csv_column_topk生成)
topk:                        # 直接是数组
  - value: xxx
    count: int
    percentage: float

# AI描述 (由db_column_semantic/csv_semantic生成)
semantic_summary: str|null
```

## `.view` - Database View

```yaml
created_at: str
# 同.table结构
row_count: int|null
column_count: int|null
semantic_summary: str|null
```

## `.fk` - Foreign Key Relation

```yaml
relation_type: str           # "foreign_key" | "naming_convention"
from_table: str
from_column: str
to_table: str
to_column: str
confidence: float           # 0.0-1.0
created_at: str
```

## `.csv` / `.tsv`

```yaml
path: str
modified_at: str
created_at: str
row_count: int
column_count: int
file_size: int
delimiter: str              # "," or "\t"
semantic_summary: str|null  # AI描述
```

## `.json`

```yaml
path: str
modified_at: str
created_at: str
file_size: int
line_count: int
char_count: int
structure_type: str        # "object" | "array" | ...
top_level_keys: [str]      # 对象类型时有
key_count: int             # 对象类型时有
array_length: int          # 数组类型时有
semantic_summary: str|null
```

## `.yaml`

```yaml
path: str
modified_at: str
created_at: str
file_size: int
line_count: int
char_count: int
structure_type: str        # "mapping" | "sequence"
top_level_keys: [str]      # mapping类型时有
key_count: int
sequence_length: int       # sequence类型时有
semantic_summary: str|null
```

## `.xml`

```yaml
path: str
modified_at: str
created_at: str
file_size: int
line_count: int
char_count: int
structure_type: "xml"
root_element: str
child_elements: [str]
semantic_summary: str|null
```

## `.toml`

```yaml
path: str
modified_at: str
created_at: str
file_size: int
line_count: int
char_count: int
structure_type: "table"
top_level_keys: [str]
key_count: int
semantic_summary: str|null
```

## `.md` - Markdown

```yaml
path: str
modified_at: str
created_at: str
file_size: int
line_count: int
char_count: int
structure_type: "markdown"
heading_count: int
code_block_count: int
link_count: int
semantic_summary: str|null
```

## `.txt` / `.log` / `.sql` / `.py` / `.js` / `.ts` / etc.

```yaml
path: str
modified_at: str
created_at: str
file_size: int
encoding: str               # e.g., "utf-8"
char_count: int
line_count: int
empty_line_count: int
non_empty_line_count: int
avg_line_length: float
max_line_length: int
letter_count: int
digit_count: int
space_count: int
punct_count: int
other_count: int
semantic_summary: str|null
```

## `.chunk` - Text Chunk

```yaml
chunk_index: int
char_count: int
created_at: str
# _raw 文件包含实际文本内容
```

## `.rel` - Logical Relation (AI推断)

```yaml
relation_type: str          # "logical" | "semantic"
from_table: str
from_column: str
to_table: str
to_column: str
confidence: float
inference_method: str      # "jaccard" | "embedding" | "llm"
created_at: str
```

## `.overlap` - Column Overlap

```yaml
# Jaccard相似度检测出的列重叠
from_table: str
from_column: str
to_table: str
to_column: str
jaccard_score: float       # 0.0-1.0
overlap_count: int
unique_values_from: int
unique_values_to: int
created_at: str
```
