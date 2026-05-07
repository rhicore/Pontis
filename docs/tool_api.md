# Pontis Tool API 实测文档

测试数据：financial 数据库（197 节点），路径 `example_data/bird/dev_databases/financial`

---

## 统一参数 `ref`

所有工具的实体引用参数统一为 `ref`，匹配逻辑一致：

| ref 形式 | 匹配行为 |
|---|---|
| `loan` | 精确名称 |
| `loan*` | 名称 glob |
| `*:table` | 标签 AND |
| `*:col:INT` | 多标签 AND |
| `*:table\|view` | 标签 OR |
| `*.sqlite/*:table` | 多跳遍历 |
| `*.sqlite/**/*:col` | 变长遍历 |

写工具（update_meta / delete / add_edge）额外约束：`ref` 必须唯一匹配。实体 ID（ent_xxx）为内部属性，不对外暴露。

---

## 1. glob — URN 图查询

参数：`ref`（必填）、`offset`、`limit`

### 精确名称
```
glob_command('loan')
→ loan	:table	- rows, - cols | 1 fk | 贷款表，记录所有贷款信息
```

### 通配符
```
glob_command('*.sqlite')
→ financial.sqlite	:file:db	68.0 MB | 8 tables, 0 views | financial 数据库主文件
```

### 标签过滤
```
glob_command('*:table', limit=3)
→ disp	:table	- rows, - cols | 3 fks
  order	:table	- rows, - cols | 1 fk
  card	:table	- rows, - cols | 1 fk
  (共 8 条结果，当前显示第 1-3 条。使用 offset=3 查看后续结果)
```

### 多标签 AND
```
glob_command('*:col:INT', limit=5)
→ disp_id	:col:INT	-
  district_id	:col:INT	-
  account_id	:col:INT	-
  account_id	:col:INT	-
  disp_id	:col:INT	-
  (共 28 条结果，当前显示第 1-5 条。使用 offset=5 查看后续结果)
```

### 多跳遍历
```
glob_command('*.sqlite/*:table', limit=3)
→ account	:table	- rows, - cols
  order	:table	- rows, - cols | 1 fk
  loan	:table	- rows, - cols | 1 fk | 贷款表，记录所有贷款信息
  (共 8 条结果...)
```

### 不存在
```
glob_command('nonexistent')
→ No objects found
```

**返回格式**：`name\t:labels\tinfo`，分页时末尾提示翻页。

---

## 2. meta — 元数据查看

参数：`ref`（必填）、`property`、`all`

### 表实体
```
meta_command('loan')
→ financial::	loan	:table

  primary_key: loan_id
  fk:   loan.account_id__to__account.account_id	:fk	loan.account_id → account.account_id 外键
  brief: 贷款表，记录所有贷款信息
```

### 列实体
```
meta_command('balance')
→ financial::	balance	:col:INT

  cardinality: 111042
  null_count: 0
  null_percentage: 0.0
  sample: 1000, 4679, 20977, 26835, 30415, 28903, 22714, 23318, 21721, 20249
  min_value: -41126
  max_value: 209637
  mean_value: 38518.3791
```

### 指定 property
```
meta_command('loan', property='row_count')
→ 未找到: row_count. 可用字段: _labels, brief, col, created_at, file, fk, overlap, primary_key
```

### 不存在
```
meta_command('nonexistent')
→ No metadata found for 'nonexistent'
```

---

## 3. search — BM25 语义检索

参数：`ref`（scope 过滤）、`query`（必填，搜索文本）、`offset`、`limit`

### 全文搜索
```
search_command('*', 'account balance', limit=5)
→ financial::	loan.account_id__to__account.account_id	:fk	loan.account_id → account.account_id 外键
  financial::	trans.account_id__to__account.account_id	:fk	trans.account_id → account.account_id 外键
  financial::	account.district_id__to__district.district_id	:fk	account.district_id → district.district_id 外键
  financial::	order.account_id__to__account.account_id	:fk	order.account_id → account.account_id 外键
  financial::	disp.account_id__to__account.account_id	:fk	disp.account_id → account.account_id 外键
  (共 21 条结果，当前显示第 1-5 条。使用 offset=5 查看后续结果)
```

### scope 过滤
```
search_command('trans*', 'balance')
→ No objects found
```
（trans* 范围内的实体 brief/detail 不含 "balance"）

---

## 4. cypher — 直接 Cypher 查询

参数：`query`（必填）、`offset`、`limit`

### 按标签
```
cypher_command('MATCH (n:table) RETURN n')
→ 共 8 条结果（显示 1-8）
  n: disp [:table]
  n: order [:table]
  n: card [:table]
  n: account [:table]
  n: loan [:table]
  n: district [:table]
  n: trans [:table]
  n: client [:table]
```

### 1-hop 遍历
```
cypher_command('MATCH (a:table)--(b:col) RETURN a, b', limit=3)
→ 共 95 条结果（显示 1-3）
  a: account [:table]  b: data_format [:col:TEXT]
  a: account [:table]  b: column_name [:col:TEXT]
  a: account [:table]  b: original_column_name [:col:TEXT]
```

### WHERE 精确匹配
```
cypher_command('MATCH (n) WHERE n.name = "loan" RETURN n')
→ 共 1 条结果（显示 1-1）
  n: loan [:table]
```

---

## 5. resolve_entity — 内部解析

（非工具，供写工具内部调用）

### 精确名称
```
resolve_entity('loan') → ('ent_14a60ee2', None)
```

### glob 多个→报错
```
resolve_entity('*:table')
→ (None, '匹配到 8 个实体，请使用更精确的模式:\n  disp\n  order\n  ...')
```

### 不存在
```
resolve_entity('nonexistent') → (None, '未找到匹配的实体: nonexistent')
```

---

## 6. update_meta — 更新元数据

参数：`ref`（必填，唯一匹配）、`fields`（必填，`{brief?, detail?}`）

### 精确名称
```
update_meta_command('loan', {'brief': 'updated brief'})
→ OK loan:
  brief: updated brief
```

---

## 7. create_entity — 创建实体

参数：`ref`（必填，精确名称，不允许通配符）、`meta`、`edges`

**限制**（类似 mkdir）：
- `ref` 不允许通配符 `*` `?` `[]`
- `ref` 必须匹配允许的实体类型
- 不自动连边，`edges` 参数显式指定

### `/` 路径风格的关系实体
```
create_entity_command('loan/balance/account/balance.rel',
    {'brief': 'loan→account FK'},
    edges=[{'a': 'loan', 'b': 'account'}])
→ Created: loan/balance/account/balance.rel
  Edges (1):
    loan ↔ account
```

### 知识实体
```
create_entity_command('testing.convention', {'brief': 'test convention'})
→ Created: testing.convention
```

### 通配符拒绝
```
create_entity_command('test*.convention')
→ 错误: 实体名不允许包含通配符 (*, ?, [])
```

---

## 8. add_edge — 添加边

参数：`edges`（必填，`[{a: ref, b: ref}, ...]`）

### 精确名称
```
add_edge_command([{'a': 'loan', 'b': 'account'}])
→ 已添加 1 条边:
  loan ↔ account
```

### 不存在端点
```
add_edge_command([{'a': 'nonexistent', 'b': 'loan'}])
→ 跳过 a: 未找到匹配的实体: nonexistent
```

---

## 9. delete — 删除实体

参数：`ref`（必填，唯一匹配）

### 正常删除
```
delete_command('loan/balance/account/balance.rel')
→ 已删除 1 个节点:
  - loan/balance/account/balance.rel
```

### 不存在
```
delete_command('nonexistent_entity')
→ Error: 未找到匹配的实体: nonexistent_entity
```

---

## 物理文件工具（不经过图谱）

| 工具 | 参数 | 用途 |
|---|---|---|
| grep | `pattern`（必填）、`path`、`output_mode`、`glob`、`ignore_case`、`head_limit`、`offset` | ripgrep 文件内容搜索 |
| bash | `command`（必填）、`timeout` | Shell 命令执行 |
| query | `sql`（必填）、`file`（必填）、`limit` | SQLite/DuckDB SQL 查询 |

---

## 参数总览

| 工具 | 必填参数 | 可选参数 | 类型 |
|---|---|---|---|
| glob | `ref` | offset, limit | 只读 |
| meta | `ref` | property, all | 只读 |
| search | `ref`, `query` | offset, limit | 只读 |
| cypher | `query` | offset, limit | 只读 |
| grep | `pattern` | path, output_mode, glob, ignore_case, head_limit, offset | 只读(物理) |
| bash | `command` | timeout | 只读(物理) |
| query | `sql`, `file` | limit | 只读(物理) |
| create_entity | `ref` | meta, edges | 写入 |
| update_meta | `ref`, `fields` | — | 写入 |
| add_edge | `edges` | — | 写入 |
| delete | `ref` | — | 写入 |

---

## 本次变更

### 显示层
1. **删除 ent_id**：glob、cypher、meta、search 不再显示 `ent_xxx`
2. **项目分隔符**：`://` → `::`（如 `financial:: loan`）
3. **标签格式**：`:col :INT` → `:col:INT`（无空格）

### 工具层
4. **create_entity**：
   - 不再从名称解析自动连边（移除 `__to__` 解析）
   - 实体名使用 `/` 路径风格（如 `loan/account_id/account/account_id.rel`）
   - 边通过 `edges` 参数显式指定
5. **search bug**：修复了 `ref` 参数 shadow 导致实体名显示为 `*` 的问题

### 存储层
6. **Cypher 标签错位**：`_execute_single` 和 `_seed_nodes` 直接遍历 `_id_index.items()`，不再通过 `_name_to_id()` 查找（避免重名实体返回错误 ID）
7. **glob 重复行**：上述修复同时消除了 `*:col:INT` 的重复行

### Extractor
8. **去掉冗余 brief**：`db_table_relations`、`db_column_overlap`、`ai_db_column_rel` 不再自动生成结构性的 brief（如 "x → y 外键"），只保留 detail
9. **列添加 brief**：`db_basic` 为列实体添加 `brief: "{col_name} ({col_type})"`
