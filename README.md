# Pontis VFS

Pontis VFS（Virtual File System）是一个多模态数据源的元数据提取和虚拟文件系统，将复杂的数据结构（数据库、CSV、JSON、Markdown 等）转换为统一的树状虚拟文件系统，使 LLM Agent 能够使用熟悉的文件操作命令（ls、stat、search、find）来探索和理解数据。

## 核心概念

传统的数据探索需要针对每种数据源使用不同的工具和语法：
- 数据库需要 SQL 和数据库客户端
- JSON 需要专门的解析器
- CSV 需要表格处理工具

**Pontis VFS 的创新**：将所有数据源统一转换为虚拟文件系统结构，使用 `ls`、`stat`、`search`、`find` 等标准文件操作即可探索任何数据。

## 支持的文件类型

| 类型 | 扩展名 | 说明 |
|------|--------|------|
| Directory | - | 物理目录，包含文件和子目录 |
| DB | .db, .sqlite, .sqlite3, .duckdb | SQLite/DuckDB 数据库 |
| Table | - | 数据库中的物理表 |
| View | - | 数据库中的视图 |
| Column | - | 表中的列（数值型、字符串型等） |
| CSV | .csv | CSV 表格文件 |
| JSON | .json, .jsonl | JSON 数据文件（简化处理） |
| Markdown | .md, .markdown | Markdown 文档 |

## 项目结构

```
Pontis/
├── extractor/              # 元数据提取引擎
│   ├── __main__.py         # 【入口】提取工具 (python -m extractor)
│   ├── base.py             # 基础提取器类
│   ├── directory.py        # 目录扫描器
│   ├── db.py               # 数据库提取器
│   ├── table.py            # 表/视图提取器
│   ├── column.py           # 列统计提取器
│   ├── json.py             # JSON 提取器
│   ├── csv.py              # CSV 提取器
│   ├── markdown.py         # Markdown 提取器
│   └── engine.py           # 提取引擎协调器
│
├── tool_use/               # LLM Agent 工具
│   ├── __main__.py         # 【入口】CLI 工具 (python -m tool_use)
│   ├── vfs.py              # 虚拟文件系统接口
│   └── tools.py            # 工具函数 (ls, stat, search, find)
│
├── common/                 # 共享模块
│   ├── schemas/            # Pydantic 数据模型
│   │   ├── base.py
│   │   ├── directory.py
│   │   ├── db.py
│   │   ├── table.py
│   │   ├── column.py
│   │   ├── json.py
│   │   ├── csv.py
│   │   └── markdown.py
│   ├── config.py           # 配置管理
│   ├── utils.py            # 工具函数
│   └── pontis.yml          # 默认配置
│
└── pyproject.toml          # 项目配置
```

## 安装

```bash
# 使用 uv 安装依赖
uv sync
```

## 使用方法

### 1. 元数据提取

使用 `extractor` 模块从数据源提取元数据，生成 `.pontis` 影子目录：

```bash
# 基本用法
uv run python -m extractor ./data

# 详细输出
uv run python -m extractor ./data -v

# 使用自定义配置
uv run python -m extractor ./data -c pontis.yml
```

### 2. 元数据浏览

使用 `tool_use` 模块浏览和查询已提取的 `.pontis` 目录：

```bash
# 列出目录内容
uv run python -m tool_use ls ./data/.pontis

# 查看节点详情
uv run python -m tool_use stat ./data/.pontis db/mydb.db/orders

# 搜索关键词
uv run python -m tool_use search ./data/.pontis "customer"

# 按模式查找
uv run python -m tool_use find ./data/.pontis "*.db"

# 交互式 shell
uv run python -m tool_use shell ./data/.pontis
```

### 可用命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `ls [path]` | 列出目录内容 | `ls db/sales.db` |
| `stat <path>` | 查看节点详细信息 | `stat db/sales.db/orders` |
| `search <query>` | 全局语义搜索 | `search "customer"` |
| `find <pattern>` | 按通配符查找 | `find "*.db"` |
| `cd <path>` | 切换目录（shell 模式）| `cd db/sales.db` |
| `pwd` | 显示当前路径（shell 模式）| `pwd` |

## Schema 设计（扁平化）

所有元数据采用扁平化结构，`_meta.yml` 中不使用嵌套：

### Directory（目录）
```yaml
type: Directory
name: my_folder
child_count: 10      # 子项总数
file_count: 7        # 文件数
subdir_count: 3      # 子目录数
```

### DB（数据库）
```yaml
type: DB
name: sales.db
dialect: SQLite
table_count: 5       # 表数量
view_count: 2        # 视图数量
```

### Table（物理表）
```yaml
type: Table
name: orders
row_count: 10000     # 行数
column_count: 8      # 列数
primary_key: order_id
joins: []            # 可 join 的表（扁平列表）
```

### View（视图）
```yaml
type: View
name: order_summary
row_count: 10000
column_count: 5
primary_key: order_id
base_tables: [orders, customers]  # 依赖的原始表
view_definition: "SELECT ..."     # 视图定义
joins: []
```

### Column（列）
```yaml
type: Column
name: price
data_type: REAL      # 列类型：INTEGER, REAL, TEXT, BLOB 等
cardinality: 500     # 唯一值数量（ls 显示 "Distinct: 500"）
null_count: 10
null_percentage: 0.1
min_value: 1.99      # 数值型统计
max_value: 999.99
mean_value: 45.50
# 或字符串统计
# min_length: 5
# max_length: 20
# avg_length: 12.5
top_k:              # Top K 频繁值（扁平列表）
  - value: 29.99
    count: 150
samples: [19.99, 29.99, 49.99]
```

### CSV（CSV 文件）
```yaml
type: CSV
name: data.csv
row_count: 1000
column_count: 5
delimiter: ","
has_header: true
encoding: utf-8
```

### JSON（JSON 文件）
```yaml
type: JSON
name: config.json
record_count: 1
is_array: false
top_level_keys: [database, settings, users]
```

### Markdown（Markdown 文档）
```yaml
type: Markdown
name: readme.md
line_count: 150
char_count: 5000
word_count: 800
heading_count: 5
code_block_count: 3
link_count: 10
image_count: 2
first_paragraph: "This is the first paragraph..."
```

## 虚拟文件系统结构示例

假设物理目录结构：
```
data/
├── sales.db
└── readme.md
```

执行提取后生成的 `.pontis` 结构：
```
data/.pontis/
├── _meta.yml                    # 根目录元数据
├── sales.db/
│   ├── _meta.yml               # 数据库元数据（类型、表数量）
│   ├── orders/                 # 表
│   │   ├── _meta.yml          # 表元数据（行数、列数）
│   │   ├── order_id/          # 列
│   │   │   └── _meta.yml     # 列元数据（cardinality、样本值等）
│   │   ├── customer_id/
│   │   │   └── _meta.yml
│   │   └── ...
│   └── customers/
│       ├── _meta.yml
│       └── ...
└── readme.md/
    └── _meta.yml               # Markdown 元数据
```

## 架构说明

### 完全解耦的设计

- **extractor 模块**：负责元数据提取，生成 `.pontis` 目录
  - 仅依赖 `extractor/` 和 `common/` 模块
  - 可独立运行，无需 tool_use

- **tool_use 模块**：负责浏览 `.pontis` 目录
  - 仅依赖 `tool_use/` 和 `common/` 模块
  - 可独立运行，无需 extractor

- **common 模块**：共享组件
  - `schemas/` - Pydantic 数据模型（两者共用）
  - `config.py` - 配置管理
  - `utils.py` - 工具函数
  - `pontis.yml` - 默认配置

### 容错设计

所有提取器具备高容错性：
- 遇到无法解析的文件记录 Warning 并继续
- 空表/空文件正常处理
- 权限错误优雅处理
- 单个文件失败不影响整体扫描

## 配置

创建 `pontis.yml` 自定义配置：

```yaml
# 目录设置
pontis_dir_name: ".pontis"
meta_filename: "_meta.yml"

# 支持的文件扩展名
db_extensions: [".db", ".sqlite", ".sqlite3", ".duckdb"]
csv_extensions: [".csv"]
json_extensions: [".json", ".jsonl"]
md_extensions: [".md", ".markdown"]

# 提取设置
sample_size: 100
top_k: 5

# 日志
log_level: "INFO"
```

## 设计原则

1. **扁平化结构**：所有 `_meta.yml` 中的属性都是扁平的，无嵌套层级
2. **类型区分**：通过 `type` 字段区分节点类型，而非继承结构
3. **统计直观**：`ls` 命令显示最具代表性的统计信息
4. **完全独立**：提取和浏览完全解耦，可分别部署使用

## License

MIT License
