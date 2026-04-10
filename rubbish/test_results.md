# Pontis VFS 命令测试结果

测试数据: `bird(new we don't use)`
测试时间: 2026-04-08

## 1. ls 命令 - 列出物理文件

### 1.1 列出根目录
```bash
$ python pontis_cli.py ls "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)"
```

输出:
```
[Type]     | [Name]                           | [Info]               | [Brief]
-------------------------------------------------------------------------------------
Dir        | dev_databases/                   | -                    |
File       | dev.json                         | array[1534]          |
File       | dev.sql                          | -                    |
File       | dev_databases.zip                | -                    |
File       | dev_tables.json                  | array[11]            |
File       | dev_tied_append.json             | array[42]            |
```

### 1.2 列出子目录
```bash
$ python pontis_cli.py ls "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" dev_databases
```

输出:
```
[Type]     | [Name]                           | [Info]               | [Brief]
-------------------------------------------------------------------------------------
Dir        | california_schools/              | -                    |
Dir        | card_games/                      | -                    |
Dir        | codebase_community/              | -                    |
Dir        | debit_card_specializing/         | -                    |
Dir        | european_football_2/             | -                    |
Dir        | financial/                       | -                    |
Dir        | formula_1/                       | -                    |
Dir        | student_club/                    | -                    |
Dir        | superhero/                       | -                    |
Dir        | thrombosis_prediction/           | -                    |
Dir        | toxicology/                      | -                    |
```

---

## 2. glob 命令 - 搜索知识图谱实体

### 2.1 搜索所有表
```bash
$ python -m tool_use.glob "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" \
  dev_databases/financial/financial.db "*.table"
```

输出:
```
Entities under dev_databases/financial/financial.db::_entity/:
[Type]     | [Name]                           | [Info]               | [Brief]
-------------------------------------------------------------------------------------
Unknown    | account.table                    | -                    |
Unknown    | card.table                       | -                    |
Unknown    | client.table                     | -                    |
Unknown    | disp.table                       | -                    |
Unknown    | district.table                   | -                    |
Unknown    | loan.table                       | -                    |
Unknown    | order.table                      | -                    |
Unknown    | trans.table                      | -                    |
```

### 2.2 搜索 california_schools 的所有表
```bash
$ python -m tool_use.glob "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" \
  dev_databases/california_schools/california_schools.db "*.table"
```

输出:
```
Entities under dev_databases/california_schools/california_schools.db::_entity/:
[Type]     | [Name]                           | [Info]               | [Brief]
-------------------------------------------------------------------------------------
Unknown    | frpm.table                       | -                    |
Unknown    | satscores.table                  | -                    |
Unknown    | schools.table                    | -                    |
```

### 2.3 搜索列实体（列名匹配模式）
```bash
$ python -m tool_use.glob "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" \
  dev_databases/financial/financial.db "*account*.col"
```

输出:
```
No entities matching '*account*.col' found under dev_databases/financial/financial.db
```

> 注: 列实体需要运行 extractor 生成骨架后才能搜索到。

---

## 3. meta 命令 - 查看元数据

### 3.1 查看物理文件元数据
```bash
$ python -m tool_use.meta "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" \
  dev_databases/financial/financial.db
```

输出:
```
path: dev_databases/financial/financial.db
created_at: 2026-04-08T22:43:50.801599
modified_at: 2026-04-06T04:21:35.480413
table_count: 0
view_count: 0
index_count: 0
file_size: 0
```

> 注: 此数据库文件大小为0，实际数据可能在 .sqlite 文件中。

### 3.2 查看实体元数据
```bash
$ python -m tool_use.meta "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" \
  dev_databases/financial/financial.db account.table
```

输出:
```
created_at: 2026-04-08T22:43:50.072417
```

### 3.3 查看 JSON 文件元数据
```bash
$ python -m tool_use.meta "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" dev.json
```

输出:
```
path: dev.json
modified_at: 2026-04-03T15:02:26.851950
created_at: 2026-04-08T22:43:48.874672
structure_type: array
array_length: 1534
```

---

## 4. read 命令 - 读取内容

### 4.1 读取文本文件
```bash
$ python -m tool_use.read "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" dev.sql -l 20
```

输出:
```sql
   1 | PRAGMA foreign_keys=OFF;
   2 | BEGIN TRANSACTION;
   3 | CREATE TABLE "Account" (
   4 |   "account_id" INTEGER PRIMARY KEY AUTOINCREMENT,
   5 |   "district_id" INTEGER NOT NULL,
   6 |   "frequency" TEXT NOT NULL,
   7 |   "parseddate" TEXT,
   8 |   "year" INTEGER,
   9 |   "month" INTEGER,
  10 |   "day" INTEGER,
  11 |   "fulldate" TEXT
  12 | );
  13 | INSERT INTO "Account" VALUES(1,18,'POPLATEK MESICNE','1995-03-24',1995,3,24,'1995-03-24');
  14 | INSERT INTO "Account" VALUES(2,1,'POPLATEK MESICNE','1993-02-26',1993,2,26,'1993-02-26');
  15 | INSERT INTO "Account" VALUES(3,5,'POPLATEK MESICNE','1994-08-03',1994,8,3,'1994-08-03');

... (more lines, total 3128)
```

### 4.2 读取 JSON 文件（带 offset）
```bash
$ python -m tool_use.read "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" dev_tables.json -o 1 -l 10
```

输出:
```json
   1 | [
   2 |   {
   3 |     "db_id": "california_schools",
   4 |     "table_names": [
   5 |       "frpm",
   5 |       "satscores",
   6 |       "schools"
   7 |     ],
   8 |     "column_names": {
   9 |       "frpm": [
  10 |         "CDSCode",

... (more lines, total 8120)
```

### 4.3 读取数据库表（当前有路径问题）
```bash
$ python -m tool_use.read "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" \
  dev_databases/financial/financial.db account.table
```

输出:
```
Error: Table 'account' not found
```

> 问题说明: 数据库文件命名不一致（.db vs .sqlite），导致 extractor 无法正确关联物理文件和数据库。

---

## 5. jd 命令 - JSON/YAML 结构查看

### 5.1 查看 JSON 文件顶层结构
```bash
$ python -m tool_use.jd "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" dev_tables.json ROOT
```

输出:
```
Structure of: dev_tables.json/ROOT

[HasSub] | [Name]                           | [Info]               | [Brief]
-------------------------------------------------------------------------------------
[+]      | [0]                              | dict{8}              | keys: db_id, tabl...
[+]      | [1]                              | dict{8}              | keys: db_id, tabl...
[+]      | [2]                              | dict{8}              | keys: db_id, tabl...
[+]      | [3]                              | dict{8}              | keys: db_id, tabl...
[+]      | [4]                              | dict{8}              | keys: db_id, tabl...
[+]      | [5]                              | dict{8}              | keys: db_id, tabl...
[+]      | [6]                              | dict{8}              | keys: db_id, tabl...
[+]      | [7]                              | dict{8}              | keys: db_id, tabl...
[+]      | [8]                              | dict{8}              | keys: db_id, tabl...
[+]      | [9]                              | dict{8}              | keys: db_id, tabl...
[+]      | [10]                             | dict{8}              | keys: db_id, tabl...

Use 'jd <file> <path>' to navigate deeper
```

### 5.2 查看嵌套结构
```bash
$ python -m tool_use.jd "/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)" dev_tables.json ROOT.0
```

输出（示例）:
```
Structure of: dev_tables.json/ROOT.0

[HasSub] | [Name]                           | [Info]               | [Brief]
-------------------------------------------------------------------------------------
         | db_id                            | california_schools   | -
[+]      | table_names                      | array[3]             | -
[+]      | column_names                     | dict{3}              | keys: frpm, satsc...
[+]      | column_types                     | dict{3}              | keys: frpm, satsc...
         | db_table_names                   | california_schools   | -
[+]      | db_column_names                  | dict{3}              | keys: frpm, satsc...
         | pk                               | dict{3}              | -
         | fk                               | dict{3}              | -
```

---

## 6. 目录结构

### 6.1 .pontis 影子目录结构

```
.pontis/
├── dev_databases/
│   ├── california_schools/
│   │   ├── california_schools.db/          # 物理文件映射
│   │   │   ├── _meta.yml                   # 文件元数据
│   │   │   └── _entity/                    # 逻辑实体文件夹
│   │   │       ├── frpm.table/
│   │   │       ├── satscores.table/
│   │   │       └── schools.table/
│   │   └── database_description/
│   ├── financial/
│   │   ├── financial.db/
│   │   │   ├── _meta.yml
│   │   │   └── _entity/
│   │   │       ├── account.table/
│   │   │       ├── card.table/
│   │   │       ├── client.table/
│   │   │       ├── disp.table/
│   │   │       ├── district.table/
│   │   │       ├── loan.table/
│   │   │       ├── order.table/
│   │   │       └── trans.table/
│   │   └── database_description/
│   └── ...
├── dev.json
├── dev.sql
├── dev_databases.zip
├── dev_tables.json
└── dev_tied_append.json
```

---

## 7. 命令语法总结

### 7.1 新语法（推荐）
```bash
# glob: 搜索实体
python -m tool_use.glob <project> <physical_file> [pattern]

# meta: 查看元数据
python -m tool_use.meta <project> <physical_file> [entity] [options]

# read: 读取内容
python -m tool_use.read <project> <physical_file> [entity] [options]
```

### 7.2 交互式 shell
```bash
$ python pontis_cli.py <project>

# 在 shell 中使用
> ls [path]
> cd <path>
> glob <file> [pattern]
> meta <file> [entity] [options]
> read <file> [entity] [options]
```

---

## 8. 已知问题

1. **数据库文件命名不一致**: 实际文件为 `.sqlite`，但映射为 `.db`，导致 extractor 无法正确读取数据库内容。

2. **实体类型识别**: 当前实体类型显示为 "Unknown"，需要完善类型检测逻辑。

3. **Info 和 Brief 字段**: 需要运行更多 extractor 阶段（如 db_column_stats, db_column_sample）来填充这些字段。

---

## 9. 后续改进建议

1. 修复数据库文件路径解析问题
2. 运行完整的 extractor 流程获取统计信息
3. 添加 AI 语义总结模块
4. 完善实体类型识别
