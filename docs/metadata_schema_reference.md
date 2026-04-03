# Pontis 元数据 Schema 参考文档

本文档总结了 Pontis 系统中所有数据类型的元数据结构，用于与 LLM 交流时作为参考。

---

## 1. 基础类型定义

### 1.1 NodeType (节点类型枚举)
所有支持的节点类型：
- `Directory` - 目录
- `DB` - 数据库文件
- `Table` - 数据表
- `View` - 视图
- `Column` - 列
- `JSON` - JSON 文件
- `CSV` - CSV 文件
- `Markdown` - Markdown 文档

### 1.2 DataType (列数据类型枚举)
列支持的数据类型：
- `INTEGER` - 整数
- `REAL` - 浮点数
- `TEXT` - 文本
- `BLOB` - 二进制数据
- `BOOLEAN` - 布尔值
- `DATETIME` - 日期时间
- `JSON` - JSON 数据
- `VARCHAR` - 变长字符串
- `UNKNOWN` - 未知类型

---

## 2. 所有节点共有的基础字段 (BaseNode)

每个节点类型都继承这些字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `type` | NodeType | **必需** 节点类型标识 |
| `name` | str | **必需** 节点名称 |
| `description` | str | 人工编写的描述（可选） |
| `short_summary` | str | AI 生成的短摘要（LLM 启用时生成） |
| `long_summary` | str | AI 生成的长摘要（LLM 启用时生成） |
| `created_at` | datetime | 创建时间（自动生成） |
| `modified_at` | datetime | 修改时间（自动生成） |

---

## 3. 各节点类型的特有字段

### 3.1 Directory (目录)
**用途**：表示文件系统中的目录

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `child_count` | int | 子项总数（文件+子目录） |
| `file_count` | int | 文件数量 |
| `subdir_count` | int | 子目录数量 |

**示例 `_meta.yml`**：
```yaml
type: Directory
name: my_data
child_count: 5
file_count: 3
subdir_count: 2
created_at: '2026-04-03T15:14:28.972000'
modified_at: '2026-04-03T15:14:28.972000'
```

---

### 3.2 DB (数据库文件)
**用途**：表示 SQLite/DuckDB 等数据库文件

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `dialect` | str | 数据库方言，如 "SQLite", "DuckDB" |
| `table_count` | int | 表数量 |
| `view_count` | int | 视图数量 |

**示例 `_meta.yml`**：
```yaml
type: DB
name: financial.sqlite
dialect: SQLite
table_count: 8
view_count: 0
created_at: '2026-04-03T15:14:29.158000'
modified_at: '2026-04-03T15:14:29.158000'
```

---

### 3.3 Table (数据表)
**用途**：表示数据库中的物理表

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `row_count` | int | 行数（记录数） |
| `column_count` | int | 列数量 |
| `primary_key` | str | 主键列名（如有） |
| `joins` | List[Dict] | Join 关系列表（见下方格式） |
| `brief` | str | AI 生成的简短描述（LLM 启用时） |
| `detail` | str | AI 生成的详细描述（LLM 启用时） |

**Join 关系格式**（每个 join 是一个字典）：
```yaml
joins:
  - target_table: district      # 目标表名
    target_column: district_id  # 目标列名
    source_column: district_id  # 当前表的列名
    confidence: 1.0             # 置信度（1.0=显式外键, <1.0=AI检测）
    comment: "Explicit foreign key"  # 说明文字
```

**示例 `_meta.yml`**：
```yaml
type: Table
name: account
row_count: 4500
column_count: 4
primary_key: account_id
joins:
  - target_table: district
    target_column: district_id
    source_column: district_id
    confidence: 1.0
    comment: "Explicit foreign key constraint from database schema"
brief: "Stores account information and metadata"  # AI 生成（可选）
detail: "Contains account records with district associations..."  # AI 生成（可选）
created_at: '2026-04-03T15:14:29.175000'
modified_at: '2026-04-03T15:14:29.175000'
```

---

### 3.4 View (视图)
**用途**：表示数据库中的视图

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `row_count` | int | 行数（估算） |
| `column_count` | int | 列数量 |
| `primary_key` | str | 主键列名（如有） |
| `base_tables` | List[str] | 该视图依赖的基表列表 |
| `view_definition` | str | 视图定义的 SQL 语句 |
| `joins` | List[Dict] | Join 关系列表（同 Table） |

**示例 `_meta.yml`**：
```yaml
type: View
name: active_users_view
row_count: 1500
column_count: 5
primary_key: user_id
base_tables:
  - users
  - user_status
view_definition: "CREATE VIEW active_users_view AS SELECT ..."
joins: []
created_at: '2026-04-03T15:14:30.000000'
```

---

### 3.5 Column (列)
**用途**：表示表中的列

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `data_type` | str | 数据类型，如 "INTEGER", "TEXT", "REAL" |
| `nullable` | bool | 是否可为 NULL |
| `cardinality` | int | 唯一值数量（去重后的值个数） |
| `null_count` | int | NULL 值数量 |
| `null_percentage` | float | NULL 值百分比 |
| `min_value` | Any | 最小值（数值列） |
| `max_value` | Any | 最大值（数值列） |
| `mean_value` | float | 平均值（数值列） |
| `min_length` | int | 最小长度（字符串列） |
| `max_length` | int | 最大长度（字符串列） |
| `avg_length` | float | 平均长度（字符串列） |
| `top_k` | List[Dict] | 最频繁的 K 个值及其计数 |
| `samples` | List[Any] | 样本值列表 |
| `brief` | str | AI 生成的字段简短说明（LLM 启用时） |
| `detail` | str | AI 生成的字段详细说明（LLM 启用时） |

**Top K 格式**：
```yaml
top_k:
  - value: "CA"      # 值
    count: 150       # 出现次数
  - value: "NY"
    count: 89
```

**示例 `_meta.yml`**：
```yaml
type: Column
name: account_id
description: Primary Key  # 如果是主键会有此标记
data_type: INTEGER
nullable: false
cardinality: 4500
null_count: 0
null_percentage: 0.0
min_value: 1
max_value: 11382
mean_value: 2786.07
top_k:
  - value: 1
    count: 1
  - value: 2
    count: 1
samples:
  - 1
  - 2
  - 3
  - 4
  - 5
brief: "Unique account identifier"  # AI 生成（可选）
detail: "Primary key, integer 1-11382, no nulls"  # AI 生成（可选）
created_at: '2026-04-03T15:14:32.418000'
modified_at: '2026-04-03T15:14:32.418000'
```

---

### 3.6 CSV (CSV 文件)
**用途**：表示 CSV 数据文件

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `row_count` | int | 行数（数据行，不含 header） |
| `column_count` | int | 列数量 |
| `delimiter` | str | 分隔符（通常是 "," 或 "\t"） |
| `has_header` | bool | 是否有表头行 |
| `encoding` | str | 文件编码，如 "utf-8" |

**示例 `_meta.yml`**：
```yaml
type: CSV
name: customers.csv
row_count: 1000
column_count: 5
delimiter: ","
has_header: true
encoding: utf-8
created_at: '2026-04-03T15:14:29.054000'
```

---

### 3.7 JSON (JSON 文件)
**用途**：表示 JSON 数据文件

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `node_type` | str | 固定为 "JSON" |
| `record_count` | int | 记录数量（如果是数组则为元素个数，否则为 1） |
| `is_array` | bool | 根节点是否为数组 |
| `top_level_keys` | List[str] | 顶层键名列表（如果是对象） |

**示例 `_meta.yml`**：
```yaml
type: JSON
name: config.json
node_type: JSON
record_count: 1
is_array: false
top_level_keys:
  - database
  - api_keys
  - settings
created_at: '2026-04-03T15:14:29.014000'
```

---

### 3.8 Markdown (Markdown 文档)
**用途**：表示 Markdown 文档文件

**特有字段**：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `line_count` | int | 行数 |
| `char_count` | int | 字符数 |
| `word_count` | int | 词数（估算） |
| `heading_count` | int | 标题数量（# 开头的行） |
| `code_block_count` | int | 代码块数量 |
| `link_count` | int | 链接数量 |
| `image_count` | int | 图片数量 |
| `first_paragraph` | str | 第一段文字（用于预览） |

**示例 `_meta.yml`**：
```yaml
type: Markdown
name: README.md
line_count: 150
char_count: 5000
word_count: 800
heading_count: 8
code_block_count: 3
link_count: 5
image_count: 2
first_paragraph: "This project provides data extraction capabilities..."
created_at: '2026-04-03T15:14:29.100000'
```

---

## 4. 目录结构示例

Pontis 生成的 `.pontis` 目录结构示例：

```
.pontis/
├── _meta.yml                    # 根目录节点 (Directory)
├── data.db/                     # 数据库节点 (DB)
│   ├── _meta.yml
│   ├── users/                   # 表节点 (Table)
│   │   ├── _meta.yml
│   │   ├── id/                  # 列节点 (Column)
│   │   │   └── _meta.yml
│   │   ├── name/
│   │   │   └── _meta.yml
│   │   └── email/
│   │       └── _meta.yml
│   └── orders/
│       ├── _meta.yml
│       ├── order_id/
│       ├── user_id/
│       └── amount/
├── config.json                  # JSON 文件节点
│   └── _meta.yml
└── README.md                    # Markdown 文件节点
    └── _meta.yml
```

---

## 5. 与 LLM 交流时的关键提示

### 5.1 判断数据类型的快捷方式
- 看 `type` 字段即可确定节点类型
- Column 节点看 `data_type` 了解数据类型
- Table 节点看 `joins` 了解关联关系

### 5.2 判断是否有 AI 生成内容
- 检查 `brief` 和 `detail` 字段是否存在
- 如果不存在，说明 LLM 未启用或生成失败

### 5.3 Join 关系的两个方向
- **正向 Join**：当前表的列指向其他表（外键）
- **反向 Join**：其他表指向当前表（被引用）
- 通过 `comment` 字段中的 "Reverse" 字样可以区分

### 5.4 字段的适用场景
- `cardinality` 高的列适合做 WHERE 条件
- `null_percentage` 高的列需要注意空值处理
- `top_k` 可用于了解值的分布情况

---

## 6. 元数据文件位置

所有元数据存储在 `.pontis/` 目录下的 `_meta.yml` 文件中：
- 每个节点对应一个目录
- 目录内包含 `_meta.yml` 文件
- 子节点是该目录下的子目录

**读取完整元数据的方法**：
```python
import yaml

# 读取表的元数据
with open('.pontis/data.db/users/_meta.yml', 'r') as f:
    table_meta = yaml.safe_load(f)

print(f"Table: {table_meta['name']}")
print(f"Rows: {table_meta.get('row_count')}")
print(f"Joins: {len(table_meta.get('joins', []))}")
```

---

*文档生成时间: 2026-04-03*
*适用于 Pontis 元数据提取系统*
