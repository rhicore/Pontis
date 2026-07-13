# Spider2-Snow Schema Visibility Policy

本文档固定 Pontis 对 Spider2-Snow 的 schema 视野口径。后续预处理 `extract` 和 `explore` 都按这里执行。

## 结论

Spider2-Snow 的 schema 认知范围应以官方仓库里的 schema folder 为准：

```text
data/Spider2/spider2-snow/resource/databases/<db_id>/
```

不要用 live Snowflake 的 `SHOW TABLES`、`SHOW OBJECTS`、`INFORMATION_SCHEMA` 结果扩展 agent 的 schema 视野。

原因是官方 Spider-Agent prompt 明确要求：

```text
Do not use this action to query INFORMATION_SCHEMA or SHOW DATABASES/TABLES;
the schema information is all stored in the /workspace/database_name folder.
```

官方 agent setup 也是把 `spider2-snow/resource/databases/<db_id>` 复制到每个 example 目录里，而不是让 agent live 枚举 Snowflake schema。

因此 Pontis 的口径是：

```text
schema discovery / schema KG: official schema folder
table/column detail: official JSON files, with DDL.csv as broader schema surface
SQL execution / value validation: live Snowflake SELECT queries
schema expansion: do not use live SHOW/INFORMATION_SCHEMA
```

## 三种不同口径

Spider2-Snow 实际存在三层 schema 口径，不能混用：

| Source | 用途 | 是否作为 Pontis schema truth |
| --- | --- | --- |
| `DDL.csv` | 官方给 agent 的 schema-level DDL，覆盖完整或更大的 relation 面 | 是 |
| `*.json` | 官方逐表详细 metadata，包含 column names/types/descriptions/sample rows | 是 |
| live Snowflake | SQL 执行和结果验证 | 否，只做执行面 |

live Snowflake 不是 schema truth，因为它会漂移，也可能受 shared database 可用性影响。例如：

- `GITHUB_REPOS_DATE`：JSON 只有 38 个 detailed relations，但 `DDL.csv` 和 live 都有 5173 个 relations。
- `AMAZON_VENDOR_ANALYTICS__SAMPLE_DATASET`、`NETHERLANDS_OPEN_MAP_DATA`：当前 live shared database 返回 unavailable，但官方 metadata 仍存在。
- Cybersyn 类库在 JSON、DDL、live 之间也存在口径差异。

## 官方视野里的实体类型

按 `data/Spider2/spider2-snow/resource/databases` 全量扫描，按 database/schema/table/view/column/foreign key 口径统计如下：

| Entity type | Count | Source | 说明 |
| --- | ---: | --- | --- |
| database | 152 | directory name | benchmark database id |
| schema | 272 | schema directory | Snowflake schema name |
| table | 13022 | `DDL.csv` 中显式 `CREATE TABLE` | 官方 DDL schema 面里的表 |
| view | 0 | `DDL.csv` 中显式 `CREATE VIEW` / `MATERIALIZED VIEW` | 官方 DDL 没有显式 view 类型 |
| unknown relation | 526 | `DDL.csv` 中有 name/description 但 DDL 为空 | 主要是 Cybersyn 类库的描述行；按表状对象保留，但类型未知 |
| detailed table metadata | 7860 | `*.json` files | 有 descriptions/sample rows；覆盖 gold tables |
| JSON columns | 522268 | `*.json.column_names` | detailed column metadata |
| DDL columns | 1247599 | parsed `DDL.csv.DDL` | DDL 表里的列，包括 DDL-only 表 |
| explicit foreign keys | 0 | `DDL.csv` / JSON | 没有结构化外键；JSON 无 FK 字段，DDL 中 `references` 命中只是列名 |

`unknown relation` 指 `DDL.csv` 中有 table name/description，但 DDL 字段为空或不可解析。它们集中在 5 个 Cybersyn 类库：

```text
US_ADDRESSES__POI: 129
WEATHER__ENVIRONMENT: 114
US_REAL_ESTATE: 111
FINANCE__ECONOMICS: 86
GLOBAL_GOVERNMENT: 86
```

## View 怎么处理

官方 schema folder 里没有可靠的独立 `VIEW` 类型：

- live Snowflake 中确实有 user views。
- 但官方 `DDL.csv` 几乎都写成 `CREATE TABLE`。
- 有些 relation 名字像 view，例如 `*_VIEW`、`V_ADMINISTRATIVE`，全量扫描共有 69 个 view-like names。

因此 Pontis 不应在官方 schema 视野里强行生成独立的 `view` 类型体系。更稳妥的建模是：

```text
relation node:
  kind = table_like_relation
  ddl_object_type = TABLE / UNKNOWN
  view_like_name = true/false
  live_object_type = optional diagnostic only
```

也就是说，`view` 可以作为 relation 的属性或命名线索，但不作为额外 schema discovery 来源。

## Foreign Key 怎么处理

官方 schema folder 里没有可用的显式外键：

- `*.json` 只有 `table_name`、`table_fullname`、`column_names`、`column_types`、`description`、`sample_rows`，没有 foreign key 字段。
- `DDL.csv` 全量扫描没有真正的 `FOREIGN KEY` 约束。
- 仅有的 `references` 命中来自列名，例如 `OPEN_TARGETS_PLATFORM_1.PLATFORM.DRUGWARNINGS."references"`，不是外键约束。

因此 Pontis 不应从 Spider2-Snow 官方 metadata 里生成 explicit FK edge。后续如果需要 join knowledge，只能来自：

```text
column-name heuristics
gold/query observations
sample/value overlap
explorer 推断
```

这些都应标记为 inferred，不应标记为 explicit foreign key。

## 不应纳入 schema 视野的实体

这些对象不应进入 Spider2-Snow 的 Pontis schema KG：

- Snowflake `INFORMATION_SCHEMA` schemas/views。
- live Snowflake 中存在但官方 folder 没给的 extra objects。
- stage、file format、procedure、function、sequence 等 live object。当前 live 审计也没有在 user schemas 中看到这类对象。
- 任何通过 `SHOW DATABASES/TABLES/OBJECTS` 或 `INFORMATION_SCHEMA` 发现的新表列。

如果 SQL execution 报错提示某表不可用，应记录为 execution/runtime issue，而不是自动改写 schema truth。

## Extract / Explore 规则

后续 Pontis 对 Spider2-Snow 应按以下规则实现：

1. `extract` 先读官方 folder。
   - 从 `<db_id>/<schema>/DDL.csv` 建 database、schema、relation、DDL column。
   - 从 `<db_id>/<schema>/*.json` 补充 detailed relation、column descriptions、sample rows。
   - JSON relation 应 merge 到对应 DDL relation；如果只有 JSON 没有 DDL，也应保留。

2. `db_table_group` 以 DDL relation 为主。
   - `GITHUB_REPOS_DATE` 这类 DDL-only 分片必须被识别。
   - JSON-only 统计会低估官方 agent 实际看到的 schema 面。

3. `explore` 只能在官方 schema KG 内做 schema exploration。
   - 可以读 DDL/JSON 节点。
   - 可以对 live Snowflake 执行候选 SELECT 或小样本 value check。
   - 不允许通过 live `SHOW` / `INFORMATION_SCHEMA` 扩展可见 schema。

4. live Snowflake 只做执行验证。
   - 可以验证 SQL 是否可执行。
   - 可以检查候选条件、枚举值、聚合结果。
   - 不作为 schema entity source。

## 对当前统计的影响

之前基于 JSON 的认知实体统计低估了少数库，尤其：

```text
GITHUB_REPOS_DATE: JSON 38, DDL/live 5173
FINANCE__ECONOMICS: JSON 50, DDL 136, live 48
GLOBAL_GOVERNMENT: JSON 50, DDL 136, live 51
WEATHER__ENVIRONMENT: JSON 22, DDL 136, live 29
US_REAL_ESTATE: JSON 25, DDL 136, live 28
US_ADDRESSES__POI: JSON 7, DDL 136, live 10
BASEBALL: JSON 2, DDL 29, live 2
```

后续如果讨论“agent 需要认知的实体数量”，应优先报两个数：

```text
official DDL relations: agent 初始 schema 面
JSON detailed relations: 可直接精读的详细表面
```

不能只报 JSON table count。
