# Pontis Extractor 测试报告

**测试日期**: 2026-04-10  
**测试数据目录**: `/tmp/pontis_test_data/`  
**Extractor版本**: master (commit e8f4a39)

---

## 1. 测试数据集

### 数据文件一览

| 文件 | 类型 | 说明 |
|------|------|------|
| `test.db` | SQLite | 3表+1视图，含INT/TEXT/REAL/BOOL列，外键关系 |
| `employees.csv` | CSV | 5行5列员工数据，含数值列(salary) |
| `config.json` | JSON | 嵌套object，含array/dict子结构 |
| `scores.json` | JSON | Array结构，含嵌套array(scores) |
| `settings.yaml` | YAML | Mapping结构 |
| `data.xml` | XML | catalog > book结构 |
| `pyproject.toml` | TOML | Table结构 |
| `README.md` | Markdown | 文本文件 |
| `notes.txt` | TXT | 纯文本 |
| `app.py` | Python | 代码文件 |

### SQLite数据库结构

```sql
-- users表 (5行, 7列)
CREATE TABLE users (
    id INTEGER PRIMARY KEY,    -- INT
    name TEXT,                  -- TEXT
    email TEXT,                 -- TEXT (含1个NULL)
    age INTEGER,                -- INT
    salary REAL,                -- REAL
    active INTEGER,             -- BOOL
    created_at TEXT             -- TEXT
);

-- orders表 (7行, 5列)
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,    -- INT
    user_id INTEGER,           -- INT (FK -> users.id)
    product TEXT,              -- TEXT
    amount REAL,               -- REAL
    status TEXT                -- TEXT
);

-- products表 (5行, 5列)
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT,
    price REAL,
    stock INTEGER,
    category TEXT
);

-- user_orders视图 (JOIN查询)
CREATE VIEW user_orders AS
    SELECT u.name, o.product, o.amount, o.status
    FROM users u JOIN orders o ON u.id = o.user_id;
```

---

## 2. 生成结果总览

### 统计

| 指标 | 数值 |
|------|------|
| 源文件数 | 10 |
| `_meta.yml`文件总数 | 51 |
| 实体类型覆盖 | `.table`, `.view`, `.col`, `.fk`, `.overlap`, `.pattern` |

### 实体分布

| 文件 | 实体数 | 实体类型 |
|------|--------|----------|
| `test.db` | 26 | 3×.table, 1×.view, 18×.col, 1×.fk, 3×.overlap |
| `employees.csv` | 5 | 5×.col |
| `config.json` | 5 | 5×.pattern |
| `scores.json` | 2 | 2×.pattern |
| `settings.yaml` | 0 | (无实体，仅元信息) |
| `data.xml` | 0 | (无实体，仅元信息) |
| `pyproject.toml` | 0 | (无实体，仅元信息) |
| `README.md` | 0 | (无实体，仅元信息) |
| `notes.txt` | 0 | (无实体，仅元信息) |
| `app.py` | 0 | (无实体，仅元信息) |

---

## 3. VFS文件结构

```
.pontis/
├── test.db/
│   ├── _meta.yml                          # DB元信息
│   ├── orders.user_id__to__users.id.fk/   # FK关系
│   │   └── _meta.yml
│   └── _entity/
│       ├── users.table/                   # 表实体
│       │   └── _meta.yml
│       ├── orders.table/
│       │   └── _meta.yml
│       ├── products.table/
│       │   └── _meta.yml
│       ├── user_orders.view/              # 视图实体
│       │   └── _meta.yml
│       ├── users.id.INT.col/              # 列实体
│       ├── users.name.TEXT.col/
│       ├── users.email.TEXT.col/
│       ├── users.age.INT.col/
│       ├── users.salary.REAL.col/
│       ├── users.active.BOOL.col/
│       ├── users.created_at.TEXT.col/
│       ├── orders.id.INT.col/
│       ├── orders.user_id.INT.col/
│       ├── orders.product.TEXT.col/
│       ├── orders.amount.REAL.col/
│       ├── orders.status.TEXT.col/
│       ├── products.id.INT.col/
│       ├── products.name.TEXT.col/
│       ├── products.price.REAL.col/
│       ├── products.stock.INT.col/
│       ├── products.category.TEXT.col/
│       ├── user_orders.name.TEXT.col/
│       ├── user_orders.product.TEXT.col/
│       ├── user_orders.amount.REAL.col/
│       ├── user_orders.status.TEXT.col/
│       ├── user_orders.amount__to__orders.amount.overlap/    # 列重叠
│       ├── user_orders.product__to__orders.product.overlap/
│       └── user_orders.status__to__orders.status.overlap/
├── employees.csv/
│   ├── _meta.yml
│   └── _entity/
│       ├── employees.emp_id.TEXT.col/
│       ├── employees.name.TEXT.col/
│       ├── employees.department.TEXT.col/
│       ├── employees.hire_date.TEXT.col/
│       └── employees.salary.TEXT.col/
├── config.json/
│   ├── _meta.yml
│   └── _entity/
│       ├── $.pattern/
│       ├── $.database.pattern/
│       ├── $.settings.pattern/
│       ├── $.tags.pattern/
│       └── $.users.pattern/
├── scores.json/
│   ├── _meta.yml
│   └── _entity/
│       ├── $.pattern/
│       └── $.[n].scores.pattern/
├── settings.yaml/
│   └── _meta.yml
├── data.xml/
│   └── _meta.yml
├── pyproject.toml/
│   └── _meta.yml
├── README.md/
│   └── _meta.yml
├── notes.txt/
│   └── _meta.yml
└── app.py/
    └── _meta.yml
```

---

## 4. 元信息详细检查

### 4.1 SQLite数据库 (`test.db`)

**文件级元信息**:
```yaml
path: test.db
modified_at: '2026-04-10T03:13:09.367439'
created_at: '2026-04-10T03:19:58.526595'
table_count: 3
view_count: 1
index_count: 0
file_size: 16384
```
**评估**: 完整。包含表/视图/索引计数、文件大小。

**表实体** — `users.table/_meta.yml`:
```yaml
created_at: '2026-04-10T03:19:58.531564'
row_count: 5
column_count: 7
primary_key: id
```
**评估**: 完整。行数、列数、主键均正确。

**视图实体** — `user_orders.view/_meta.yml`:
```yaml
created_at: '2026-04-10T03:19:58.537228'
```
**评估**: 缺少 `row_count`、`column_count`、`sql` 字段。视图元信息不够丰富。

**INT列** — `users.id.INT.col/_meta.yml`:
```yaml
source_table: users
cardinality: 5
null_count: 0
null_percentage: 0.0
min_value: 1
max_value: 5
mean_value: 3.0
```
**评估**: 完整。数值统计(min/max/mean)正确生成。

**REAL列** — `orders.amount.REAL.col/_meta.yml`:
```yaml
source_table: orders
cardinality: 7
null_count: 0
null_percentage: 0.0
min_value: 29.99
max_value: 1299.99
mean_value: 445.6371
```
**评估**: 完整。

**BOOL列** — `users.active.BOOL.col/_meta.yml`:
```yaml
source_table: users
cardinality: 2
null_count: 0
null_percentage: 0.0
```
**评估**: 缺少 `true_count` / `false_count` 等布尔特有统计。

**TEXT列 (含NULL)** — `users.email.TEXT.col/_meta.yml`:
```yaml
source_table: users
cardinality: 4
null_count: 1
null_percentage: 20.0
min_length: 15
max_length: 17
avg_length: 16.0
```
**评估**: 完整。NULL检测正确(1/5=20%)，长度统计已生成。

**外键关系** — `orders.user_id__to__users.id.fk/_meta.yml`:
```yaml
relation_type: naming_convention
from_table: orders
from_column: user_id
to_table: users
to_column: id
confidence: 0.7
```
**评估**: FK关系正确识别。但 `relation_type: naming_convention` 而非 `foreign_key`，`confidence: 0.7` 表明是基于命名推断而非数据库schema直接读取。

**列重叠** — `user_orders.amount__to__orders.amount.overlap/_meta.yml`:
```yaml
relation_type: column_overlap
from_table: user_orders
from_column: amount
from_type: REAL
to_table: orders
to_column: amount
to_type: REAL
match_type: STRONG_MATCH
reason: 'Context shared | Col tokens shared: [''amount'']'
stats:
  card_overlap: 7
  jaccard: 1.0
  cardinality_A: 7
  cardinality_B: 7
  coverage_A_in_B: 1.0
  coverage_B_in_A: 1.0
```
**评估**: 重叠检测完整且正确。视图 `user_orders` 的3列都找到了与 `orders` 表对应列的重叠关系，jaccard=1.0说明值完全一致。

### 4.2 CSV文件 (`employees.csv`)

**文件级元信息**:
```yaml
path: employees.csv
row_count: 5
column_count: 5
file_size: 219
delimiter: ','
encoding: utf-8
char_count: 213
line_count: 6
empty_line_count: 0
non_empty_line_count: 6
avg_line_length: 34.5
max_line_length: 39
letter_count: 100
digit_count: 71
space_count: 6
punct_count: 36
```
**评估**: 完整。行数、列数、分隔符、字符统计均正确。

**TEXT列 (含sample+topk)** — `employees.name.TEXT.col/_meta.yml`:
```yaml
cardinality: 5
null_count: 0
null_percentage: 0.0
sample: [Alice, Bob, Charlie, Diana, Eve]
topk:
  - value: Alice
    count: 1
    percentage: 20.0
  # ... 每个值出现1次
```
**评估**: 完整。stats/sample/topk三阶段数据全部写入。

**数值TEXT列** — `employees.salary.TEXT.col/_meta.yml`:
```yaml
cardinality: 5
null_count: 0
null_percentage: 0.0
min: 65000.0
max: 102000.0
mean: 84400.0
sample: ['95000', '72000', '88000', '65000', '102000']
topk: [...]
```
**评估**: 数值统计正确生成，虽然列类型标记为TEXT。Sample/topk也正确。

### 4.3 JSON文件

**config.json 文件级**:
```yaml
structure_type: object
top_level_keys: [app_name, version, debug, database, users, settings, tags]
key_count: 7
```

**$.pattern** (根级object):
```yaml
name: $
type: DICT
pattern: 'each pair patterns {app_name: STR, version: STR, debug: BOOL, database: DICT, users: ARRAY, settings: DICT, tags: ARRAY}'
```

**$.database.pattern** (嵌套dict):
```yaml
name: $.database
type: DICT
pattern: 'each pair patterns {host: STR, port: INT, name: STR}'
```

**scores.json $.pattern** (根级array):
```yaml
name: $
type: ARRAY
pattern: 'each item patterns {id: INT, name: STR, scores: ARRAY}'
```

**scores.json $.[n].scores.pattern** (嵌套array):
```yaml
name: $.[n].scores
type: ARRAY
pattern: each item patterns INT
```

**评估**: JSON pattern提取完整。Object和Array结构都能正确识别，嵌套路径解析正确。

### 4.4 其他序列化格式

**settings.yaml**:
```yaml
structure_type: mapping
top_level_keys: [server, logging, features]
key_count: 3
```
**评估**: 结构识别正确。

**data.xml**:
```yaml
structure_type: xml
root_element: catalog
child_elements: [book]
```
**评估**: XML根元素和子元素识别正确。

**pyproject.toml**:
```yaml
structure_type: table
top_level_keys: [project, tool, build-system]
key_count: 3
```
**评估**: TOML结构识别正确。

### 4.5 文本文件

**README.md** / **notes.txt** / **app.py** 三者均生成了文件级元信息：
```yaml
file_size, encoding, char_count, line_count, empty_line_count,
non_empty_line_count, avg_line_length, max_line_length,
letter_count, digit_count, space_count, punct_count
```
**评估**: 文本统计完整。

---

## 5. 发现的Bug及修复

### Bug 1: `find_nodes()` 排除了 `_entity/` 目录（已修复）

**位置**: `extractor/utils.py:188`  
**原因**: 目录过滤条件 `not d.startswith('_')` 将 `_entity/` 目录排除  
**影响**: 所有Phase 2+的生成器(db_table_info, db_column_stats, csv_column_stats/sample/topk, db_table_relations, db_column_overlap)静默失败，无法找到目标实体  
**修复**: 改为仅排除 `.` 开头的目录

### Bug 2: CSV生成器glob模式错误（已修复）

**位置**: `csv_column_stats.py`, `csv_column_sample.py`, `csv_column_topk.py`  
**原因**: 使用 `*.csv/*.*.*.col` 模式，而实际实体在 `*.csv/_entity/*.*.*.col`  
**影响**: CSV列的stats/sample/topk无法匹配到节点  
**修复**: 更正为 `*.csv/_entity/*.*.*.col`，并修正路径解析逻辑跳过 `_entity` 段

---

## 6. Pipeline各阶段执行情况

| 阶段 | 模块 | 状态 | 说明 |
|------|------|------|------|
| Phase 1 | `skeleton.py` | 通过 | 正确创建所有文件的VFS节点 |
| Phase 1.5 | `db_basic.py` | 通过 | 正确创建 .table/.view/.col 实体 |
| Phase 1.5 | `csv_basic.py` | 通过 | 正确创建CSV列实体 |
| Phase 1.5 | `serialized_basic.py` | 通过 | 正确分析JSON/YAML/XML/TOML结构 |
| Phase 1.5 | `text_basic.py` | 通过 | 正确创建文本文件_entity目录 |
| Phase 2 | `db_info.py` | 通过 | DB文件元信息正确 |
| Phase 2 | `db_table_info.py` | 通过 | 表的row_count/column_count/pk正确 |
| Phase 2 | `db_column_stats.py` | 通过 | INT/TEXT/REAL/BOOL列统计正确 |
| Phase 3 | `csv_info.py` | 通过 | CSV行数/列数/字符统计正确 |
| Phase 3 | `csv_column_stats.py` | 通过 | CSV列cardinality/null统计正确 |
| Phase 3 | `csv_column_sample.py` | 通过 | CSV列采样正确 |
| Phase 3 | `csv_column_topk.py` | 通过 | CSV列TopK正确 |
| Phase 4 | `json_pattern.py` | 通过 | JSON pattern提取正确 |
| Phase 5 | `text_info.py` | 通过 | 文本字符统计正确 |
| Phase 6 | `db_table_relations.py` | 通过 | FK关系识别正确 |
| Phase 7 | `db_column_overlap.py` | 通过 | 列重叠检测正确 |
| Phase 8 | (语义分析) | 跳过 | 依赖LLM，测试跳过 |
| Phase 9 | (summary) | 跳过 | 依赖LLM，测试跳过 |

---

## 7. 已知问题与不足

### 7.1 视图元信息不完整

`user_orders.view/_meta.yml` 仅包含 `created_at`，缺少：
- `row_count` / `column_count`
- `sql` (创建视图的SQL语句)
- `column_list` (列名列表)

**原因**: `db_table_info.py` 可能只处理 `.table` 类型未覆盖 `.view`。

### 7.2 BOOL列缺少布尔特有统计

`users.active.BOOL.col` 缺少 `true_count` / `false_count`。当前仅生成通用统计(cardinality/null_count)。

### 7.3 FK关系基于命名推断而非Schema

`orders.user_id__to__users.id.fk` 的 `relation_type: naming_convention` 且 `confidence: 0.7`，说明不是从数据库schema的FOREIGN KEY约束读取的，而是通过命名模式匹配推断。对于SQLite，实际的外键约束信息可通过 `PRAGMA foreign_key_list` 获取。

### 7.4 序列化文件无实体

YAML/XML/TOML文件仅生成了文件级元信息，未创建pattern实体。只有JSON文件创建了 `.pattern` 实体。这意味着序列化格式的实体创建逻辑只对JSON生效。

### 7.5 DB列缺少sample/topk数据

SQLite列实体有stats信息，但没有sample和topk数据。需要确认是否存在对应的db_column_sample/db_column_topk模块。

---

## 8. 总结

Extractor核心功能（Phase 1-7）运行正常，10种文件类型 × 6种实体类型的组合测试通过。两个关键Bug（`find_nodes`目录过滤和CSV路径解析）已修复。JSON pattern提取和DB列重叠检测是亮点功能。主要不足在于视图元信息不完整、布尔列缺少特有统计、以及FK关系未使用数据库原生约束信息。
