# Spider 2.0 表组抽取试错记录

## 目标

Spider 2.0 Snow 里有一些数据库的物理表数量远超传统 Text-to-SQL benchmark。直接让 agent 逐表读 schema，会把注意力耗在重复的分片表、版本表、年度表上，不能快速形成数据库的结构直觉。

这次 `db_table_group` extractor 的目标不是替代 table/column 级别 schema，而是在 Neo4j 里额外生成 `:table_group` 节点，让新 agent 先看到数据库的物理组织模式：

- 哪些表本质上是一组分片或版本。
- 这组表应该按时间、版本、领域 shard 还是枚举 suffix 理解。
- 这组表列结构是否稳定，能不能只读代表表。
- 如果列结构漂移，哪些列是共同列，哪些列需要进一步检查。
- 代表表是哪几张，agent 应该从哪里开始读 schema。

这之后又补了 `db_topic_group` extractor。它不是物理表组，而是更高一层的 source/topic 入口，用来解决 FEC 这类多来源混合库：

- `:source_collection`：按 schema/source 边界聚合，例如 `FEC.CENSUS_BUREAU_ACS`、`FEC.FEC`。
- `:topic_family`：在 schema 内按业务主题或数据产品聚合，例如 `individual_contributions`、`committee_master`、`zipcode_crosswalk`。

## 为什么不能只按表写 detail

Spider 2.0 的大库不是简单的“业务对象很多”，很多表是同一个逻辑对象的物理展开。典型例子：

- `GA360.GOOGLE_ANALYTICS_SAMPLE.GA_SESSIONS_20160801` 到 `GA_SESSIONS_20170801` 是 daily shard，366 张表应该先被理解成一个 time-partitioned table family。
- `GHCN_D.GHCND_YYYY` 一类表是年度分片。
- `EBI_CHEMBL` 有大量 release/version 后缀表，逐表看会把同一实体的不同版本误读成几百个不同实体。
- `FEC` 同时包含 ACS geography/year 族、正式 FEC 业务表、以及其他来源表；一级表组只能压缩部分重复模式，不能解决所有认知负担。

因此更合理的层级是：

1. database：数据库边界和主题。
2. schema/table_group：先识别重复物理模式。
3. representative table：每个表组只精读代表表和漂移列。
4. table/column：只有问题命中时再下钻。

## 当前实现位置

实现文件：

- `extractor/db_table_group.py`
- `scripts/preprocess_engine.py` 注册模块名：`db_table_group`
- `scripts/spider/extract_spider2_snow.py` 已把 `db_table_group` 放入 `EXTRACT_PIPELINE`

运行方式：

```bash
cd Pontis
uv run python -m extractor run db_table_group <project_dir>
```

Spider2 Snow pipeline 会自动调用：

```bash
uv run python scripts/spider/extract_spider2_snow.py --db GA360
```

## 表组节点契约

每个表组会写入一个 `:table_group` 节点，并建立这些关系：

```text
(db)-[:RELATED_TO]->(table_group)
(schema)-[:RELATED_TO]->(table_group)
(table)-[:RELATED_TO]->(table_group)
```

成员表节点会额外写入：

- `table_group_ref`
- `table_group_family`
- `table_group_member_count`
- `table_group_consistency`

表组节点核心属性：

- `family`：归一化后的物理表族名，例如 `GA_SESSIONS_YYYYMMDD`
- `member_count`：成员表数量
- `members`：全部成员表名
- `sample_members`：前 8 个样例成员
- `representative_member`：默认代表表
- `representative_members`：按不同列签名选出的代表表，最多 3 张
- `representative_column_signatures`：代表表的列签名摘要
- `column_count_distribution`：成员表列数分布
- `same_order_columns`：列顺序是否完全一致
- `same_column_set`：列集合是否一致
- `common_column_count`：所有成员共有列数
- `union_column_count`：成员列并集列数
- `variable_column_count`：漂移列数
- `common_columns`：共同列名，最多保留 120 个
- `variable_columns`：漂移列名，最多保留 120 个
- `common_columns_truncated` / `variable_columns_truncated`：列名列表是否截断
- `consistency`：`same_order` / `same_set` / `drifting`
- `primary_pattern_type`：主模式类型
- `cognitive_shape`：给 agent 使用的认知形状
- `agent_usage_hint`：如何使用这个表组的简短建议
- `schema_reading_strategy`：新 agent 读取该表组 schema 的具体入口

当前模式类型：

| pattern type | cognitive shape | 例子 | agent 应该怎么理解 |
| --- | --- | --- | --- |
| `date_shard_yyyymmdd` | `time_partitioned_table_family` | `GA_SESSIONS_20160801` | 按日分区表族理解 |
| `month_shard_yyyymm` | `time_partitioned_table_family` | `SALES_202401` | 按月分区表族理解 |
| `year_shard_yyyy` | `time_partitioned_table_family` | `GHCND_2023` | 按年分区表族理解 |
| `compact_year_suffix` | `time_partitioned_table_family` | `INDIV20`, `PAS220` | 按紧凑年份/选举周期后缀表族理解 |
| `quarter_shard` | `time_partitioned_table_family` | `REPORT_Q1` | 按季度分区表族理解 |
| `release_version` | `versioned_snapshot_family` | `ACTIVITIES_R35` | 按版本快照表族理解 |
| `numeric_release_version` | `versioned_snapshot_family` | `ACTIVITIES_23 ... ACTIVITIES_31` | 连续数字后缀更像 release snapshot，而不是任意枚举 shard |
| `chromosome_shard` | `domain_sharded_table_family` | `VARIANT_CHR1` | 按领域 shard 理解 |
| `geo_region_shard` | `domain_sharded_table_family` | `PLACES_TEXAS` | 按地理区域 shard 理解 |
| `numeric_suffix_shard` | `enumerated_shard_family` | `TABLE_001` | 先把数字 suffix 当物理 shard，再靠样例判断语义 |

## Source/Topic 节点契约

`db_topic_group` 会写入两类节点：

```text
(db)-[:RELATED_TO]->(source_collection)
(schema)-[:RELATED_TO]->(source_collection)
(table)-[:RELATED_TO]->(source_collection)
(table_group)-[:RELATED_TO]->(source_collection)

(db)-[:RELATED_TO]->(topic_family)
(schema)-[:RELATED_TO]->(topic_family)
(table)-[:RELATED_TO]->(topic_family)
(table_group)-[:RELATED_TO]->(topic_family)
```

核心属性：

- `group_level`：`source_collection` 或 `topic_family`
- `topic_key`：稳定主题键，例如 `individual_contributions`
- `topic_label`：给 agent 读的主题说明
- `physical_table_count`：覆盖的物理表数
- `logical_unit_count`：逻辑单元数，table_group 算 1 个逻辑单元
- `table_group_count`：包含多少物理表组
- `standalone_table_count`：包含多少未被物理表组覆盖的单表
- `sample_tables`：样例表
- `logical_units`：前若干逻辑单元，每个单元说明是 `table_group` 还是 `table`
- `dominant_table_group_shapes`：下层物理表组形状摘要
- `schema_reading_strategy`：agent 读取该 source/topic 的入口建议

成员表会写入：

- `source_collection_ref`
- `topic_group_ref`

`topic_family` 默认至少需要 2 个逻辑单元才会生成。像 GA360 这种整个 schema 只有一个 daily-shard `table_group` 的库，只生成 `source_collection`，避免增加没有信息增益的 topic 节点。

## 已验证样例

### GA360

实际跑过 `db_table_group`，结果：

```text
Table groups written: 1 groups, 366 member tables
family = GA_SESSIONS_YYYYMMDD
linked_tables = 366
consistency = drifting
common_cols = 15
union_cols = 16
sample_members = GA_SESSIONS_20160801 ... GA_SESSIONS_20160808
primary_pattern_type = date_shard_yyyymmdd
cognitive_shape = time_partitioned_table_family
representative_members = GA_SESSIONS_20160801, GA_SESSIONS_20170701
common_columns = visitorId, visitNumber, visitId, visitStartTime, date, totals, trafficSource, device, geoNetwork, customDimensions, hits, fullVisitorId, userId, channelGrouping, socialEngagementType
variable_columns = clientId
source_collection = GOOGLE_ANALYTICS_SAMPLE, physical_tables=366, logical_units=1, dominant_topics=ga_sessions
```

认知解释：

- 这不是 366 个独立业务对象，而是一个 Google Analytics session 的 daily shard。
- 新 agent 应先读 `table_group[GOOGLE_ANALYTICS_SAMPLE.GA_SESSIONS_YYYYMMDD]`，再读一两个代表表。
- `drifting` 表示不能假设所有 daily shard 列完全一样。应优先读 `representative_members`、`common_columns`，再检查 `variable_columns`。

### 离线扫描观察

用本地 Spider2 metadata 扫描过：

```bash
uv run python scripts/spider/analyze_table_groups.py --db GA360,FEC,EBI_CHEMBL,GHCN_D --top-dbs 10 --top-groups 15 --details
```

观察结果：

- 4 个 DB 共 1903 张表、84832 列。
- 当前规则识别 123 个表组，覆盖 1610/1903 张表，约 84.6%。
- `GA360`：1 组，366/366 张表被覆盖。
- `GHCN_D`：1 组，125/266 张表被覆盖。
- `EBI_CHEMBL`：80 组，700/785 张表被覆盖，很多 `*_23 ... *_31` 表应按 release/version snapshot 理解。
- `FEC`：41 组，419/486 张表被覆盖，除了 ACS geography/year 类表，也覆盖 FEC 官方短码年份表。

这说明 pattern-based table family 对“物理重复”压缩有效，但不是完整的数据库主题分类器。

补充 compact 年份后缀后，FEC 的物理表组覆盖率从 `294/486 (60.5%)` 提升到 `419/486 (86.2%)`。新增的大组包括：

```text
FEC.CMYY: 21
FEC.CNYY: 21
FEC.INDIVYY: 21
FEC.OTHYY: 21
FEC.PAS2YY: 21
FEC.CCLYY: 11
FEC.OPPEXPYY: 9
```

### FEC source/topic 离线观察

用本地 Spider2 metadata 对 `FEC` 做 topic-key 统计：

```text
source collections
- CENSUS_BUREAU_ACS: 278
- FEC: 146
- GEO_CENSUS_TRACTS: 57
- HUD_ZIPCODE_CROSSWALK: 3
- FDIC_BANKS: 2

FEC schema topics
- individual_contributions: 24
- other_committee_transactions: 24
- committee_master: 21
- pas2_contributions: 21
- candidate_master: 21
- operating_expenditures: 12
- candidate_committee_linkages: 11
- candidate_committee: 3
- candidate: 3
- committee: 3
- committee_contributions: 3
```

认知解释：

- FEC 的表多不是单一原因造成的：有 source 混合、年份切片、FEC 官方短码数据产品、地理 crosswalk。
- 新 agent 应先看 `source_collection`，确认自己是在读 ACS、FEC 官方竞选财务、FDIC、地理 tract，还是 zipcode crosswalk。
- 进入 `FEC` schema 后，应先看 `topic_family`，例如 individual contribution、committee master、candidate master，再决定是否展开具体年份/短码表。

## 试错记录

### 1. 不应该在表组模块里强制刷新 Snowflake source

最初 `db_table_group.generate()` 内部调用：

```python
workspace.refresh_sources(modules=["snowflake", "db_schema"])
```

问题：

- 这会 `force=True` 重拉 Snowflake schema。
- GA360 这种 366 张表的库会重复发布大量 table/column 节点。
- 后续写 `table_group` 时如果 query 里带 `:table` label，还可能再次触发 source module。

修正：

- 去掉显式 `refresh_sources`。
- 读查询保留 `:db/:table/:col`，让 Workspace 正常在读阶段触发 schema materialization。
- 写查询走 extractor 内部 `_write_cypher()`，避免用户查询 scope 重写和 source trigger。

### 2. 写 Cypher 不能直接走 user-facing `workspace.cypher`

Pontis 的 `workspace.cypher()` 会对用户查询做 project scope 注入。这个行为对用户查询是必要的，但 extractor 内部写图时会出问题。

踩到的错误：

```text
Can't create node `d` with labels or properties here.
The variable is already declared in this context.
```

原因：

- scope helper 会给每个节点模式注入 `project` 属性。
- 对 `MERGE (db_node)-[:RELATED_TO]->(g)` 这类已绑定节点关系模式，注入属性后会变成非法 Cypher。

修正：

- `db_table_group` 内部新增 `_write_cypher()`。
- 直接使用 active store 的 `execute_cypher()`。
- 写入节点时显式设置 `project = $project`。

### 3. 每张表单独查列太慢

最初实现是 `_load_tables()` 后对每张表再查一次列。

问题：

- GA360 还能接受。
- FEC / EBI_CHEMBL 这类库会产生几百次 Neo4j round trip。

修正：

- 改成一个 Cypher 批量读：

```cypher
MATCH (d:db)-[:RELATED_TO*1..2]-(t:table)
WHERE t._ref IS NOT NULL
OPTIONAL MATCH (t)--(c:col)
WITH d, t, c
ORDER BY d._ref, t.schema_name, t.table_name, c.ordinal_position, c.name
RETURN d, t, [name IN collect(c.name) WHERE name IS NOT NULL] AS column_names
```

### 4. 表组不是 topic ontology

`FEC` 暴露了一个关键问题：有些大库不是单纯分片，而是混合多个数据来源和统计口径。只靠 table family 不能让 agent 完整理解业务主题。

当前结论：

- `table_group` 负责压缩物理重复模式。
- 下一层可能还需要 `:topic_group` 或 `:source_collection`：
  - 按数据来源：ACS / FEC / reference tables。
  - 按业务主题：candidate / committee / contribution / geography。
  - 按文件或 schema documentation 聚合。

不要让 `table_group` 同时承担物理分片和业务 ontology 两个职责，否则会把“同一逻辑表的 366 个日期分片”和“同一业务主题下 20 张不同实体表”混在一起。

### 5. 只有列数量不够，agent 还需要直接可读的列名入口

上一版表组只写了：

```text
common_column_count
union_column_count
variable_column_count
```

这能告诉 agent “有没有漂移”，但不能告诉它“从哪张表开始看、哪些列可以安全泛化”。如果 agent 还要自己再查全部成员表才能得到共同列，那么表组的认知压缩价值不够。

修正：

- 写入 `representative_member` 和 `representative_members`。
- 按列签名选择代表表：每个不同列集合挑一个最早成员，最多 3 张。
- 写入 `common_columns` 和 `variable_columns`，并用 `*_truncated` 标记是否截断。
- 写入 `schema_reading_strategy`，把具体读法直接放在 KG 节点上。

这一步让 `table_group` 不只是统计节点，而是一个可直接进入 prompt 的 schema 理解节点。

### 6. FEC 短码不是普通表名前缀，必须做领域归一化

最初 topic key 只处理带下划线的年份模式，所以这些表没有被正确归一化：

```text
INDIV20 -> indiv20
CM20 -> cm20
CN20 -> cn20
PAS220 -> pas220
COUNTY_2021_1YR -> county_yyyy
```

问题：

- FEC 官方数据产品大量使用紧凑短码加年份后缀，没有 `_YYYY`。
- ACS 表名的 `1YR/3YR/5YR` 如果清理顺序不对，会留下无意义的 `yyyy`。
- agent 看到 `INDIV20` 和 `INDIV18` 不应该认为它们是两个主题，而应该理解成 individual contribution 的年份版本。

修正：

- `db_table_group` 对紧凑短码做物理表族归一化：
  - `INDIV20` -> `INDIVYY`
  - `CM20` -> `CMYY`
  - `CN20` -> `CNYY`
  - `PAS220` -> `PAS2YY`
  - `OPPEXP20` -> `OPPEXPYY`
- 增加 FEC 短码 alias：
  - `INDIV` -> `individual_contributions`
  - `CM` -> `committee_master`
  - `CN` -> `candidate_master`
  - `CCL` -> `candidate_committee_linkages`
  - `OPPEXP` / `OPEX` -> `operating_expenditures`
  - `OTH` / `OTHER_COMMITTEE_TX` -> `other_committee_transactions`
  - `PAS` -> `pas2_contributions`
- 先剥离 `1YR/3YR/5YR`，再剥离年份占位符。
- 增加 zipcode/census tract crosswalk alias。

连锁修正：

- `db_topic_group` 不能只理解 raw table name，还必须理解 `db_table_group` 生成的 family。
- 例如 `INDIV20` 会先变成 `INDIVYY`，topic 层仍然要把 `INDIVYY` 归到 `individual_contributions`。
- `PAS220` 会先变成 `PAS2YY`，topic 层要剥离 `YY` 和尾部数字，归到 `pas2_contributions`。

## 新 agent 使用流程

实操版查询手册见 `docs/agent/table_group_kg_playbook.md`。本节保留核心流程和设计理由。

新 agent 进入一个 Spider 2.0 Snow database 时，建议先查第一屏导航节点：

```cypher
MATCH (l:schema_landscape)
RETURN l.name, l.brief, l.table_count, l.column_count,
       l.source_collection_count, l.topic_family_count,
       l.table_group_count, l.detail
ORDER BY l.table_count DESC
```

然后查 source/schema 边界：

```cypher
MATCH (g:source_collection)
RETURN g.schema_name, g.physical_table_count,
       g.logical_unit_count, g.dominant_topics,
       g.schema_reading_strategy
ORDER BY g.physical_table_count DESC
```

然后查：

```cypher
MATCH (g:topic_family)
RETURN g.schema_name, g.topic_key, g.topic_label,
       g.physical_table_count, g.logical_unit_count,
       g.sample_tables, g.schema_reading_strategy
ORDER BY g.physical_table_count DESC
```

再下钻物理表组：

```cypher
MATCH (g:table_group)
RETURN g.family, g.member_count, g.primary_pattern_type,
       g.cognitive_shape, g.consistency,
       g.common_column_count, g.union_column_count,
       g.representative_members,
       g.common_columns, g.variable_columns,
       g.schema_reading_strategy,
       g.agent_usage_hint
ORDER BY g.member_count DESC
```

理解顺序：

1. 先看 `schema_landscape`，获得数据库第一屏导航和 prompt compression 策略。
2. 再看 `source_collection`，确认数据库是否混合了多个来源。
3. 再看 `topic_family`，确认主要业务主题和数据产品。
4. 再看 `table_group`，判断每个主题里的表数量是不是由物理分片导致。
5. 看 `cognitive_shape`：
   - `time_partitioned_table_family`：把成员当时间分区。
   - `versioned_snapshot_family`：先确定版本含义。
   - `domain_sharded_table_family`：按问题选择 shard 或全量 union。
   - `enumerated_shard_family`：谨慎，需要进一步看样例或文档。
6. 看 `consistency`：
   - `same_order` / `same_set`：代表表基本可代表整个 group。
   - `drifting`：必须检查 `common_column_count` 和 `variable_column_count`，不能直接把一张表的列推广到全组。
7. 看 `representative_members`：
   - 先读这些表的列，不要展开所有成员。
   - `drifting` 组通常会选出主要列签名和少数漂移签名的代表表。
8. 看 `common_columns` / `variable_columns`：
   - `common_columns` 可以作为表组的稳定 schema。
   - `variable_columns` 只能在命中具体成员或具体问题时使用。
9. 只有当问题指向具体日期、版本、suffix 或字段时，再下钻成员表。

面向 prompt 的压缩策略：

- 对 `same_order` 表组，只放 1 个代表表 DDL + member range。
- 对 `same_set` 表组，放 1 个代表表 DDL + 提醒列顺序可能不同。
- 对 `drifting` 表组，放 `common_columns` + `variable_columns` + 2 到 3 个 `representative_members`。
- 对 FEC 这类混合来源库，先放 source/topic 摘要，再放被问题命中的 table_group。

## FEC KG 验证记录

新增调试脚本：

```bash
uv run python scripts/spider/validate_table_group_kg_from_metadata.py --db FEC
```

这个脚本不是生产 extractor。它只用于验证 KG 节点形状：

1. 读取 Spider2-Snow 本地 JSON metadata。
2. 写入最小 `db -> schema -> table -> col` 图。
3. 复用 `db_table_group` 的 `_group_summary` / `_upsert_group`。
4. 复用 `db_topic_group` 的 source/topic summary 和 upsert。
5. 运行 deterministic `schema_landscape` explorer，写入 agent 第一屏导航节点。
6. 不触发 Snowflake live metadata refresh。

最终 FEC 验证结果：

```text
tables: 486
columns: 71832
table_groups: 41
grouped_tables: 419/486 (86.2%)
topic_groups: 32
schema_landscape: 1
```

`schema_landscape` 节点：

```text
_ref: FEC--schema_landscape
brief: FEC schema landscape: 5 sources, 27 topics, 41 table groups
table_count: 486
column_count: 71832
source_collection_count: 5
topic_family_count: 27
table_group_count: 41
```

它的 `detail` 是给新 agent 的第一屏 markdown：

1. 先列 source collections。
2. 再列 topic families。
3. 再列 top physical table groups。
4. 最后给 prompt compression 策略。

关键 table groups：

```text
FEC.CMYY       21 tables, same_set, 15 columns
FEC.CNYY       21 tables, same_set, 15 columns
FEC.INDIVYY    21 tables, same_set, 21 columns
FEC.OTHYY      21 tables, same_set, 21 columns
FEC.PAS2YY     21 tables, same_set, 22 columns
FEC.CCLYY      11 tables, same_set, 7 columns
FEC.OPPEXPYY    9 tables, same_set
```

关键 source/topic groups：

```text
source_collection CENSUS_BUREAU_ACS: 278 physical tables, 30 logical units
source_collection FEC:               146 physical tables, 16 logical units
source_collection GEO_CENSUS_TRACTS:  57 physical tables, 57 logical units

topic_family FEC.individual_contributions:       24 physical tables, 4 logical units
topic_family FEC.other_committee_transactions:   24 physical tables, 2 logical units
topic_family FEC.candidate_master:               21 physical tables, 1 logical unit
topic_family FEC.committee_master:               21 physical tables, 1 logical unit
topic_family FEC.pas2_contributions:             21 physical tables, 1 logical unit
topic_family FEC.operating_expenditures:         12 physical tables, 2 logical units
topic_family FEC.candidate_committee_linkages:   11 physical tables, 1 logical unit
```

这次验证改正了一个重要判断：`topic_family` 不能只按 `logical_unit_count >= 2` 过滤。
在 FEC 里，`CMYY`、`CNYY`、`PAS2YY`、`CCLYY` 都是一个 table_group，但它们各自覆盖大量物理表，并且 schema 内明显有多个业务主题。
因此现在规则是：

- 如果 topic 有多个 logical units，生成 `topic_family`。
- 如果 schema 内有多个 topic，并且该 topic 覆盖多张物理表，也生成 `topic_family`。
- 如果 schema 本身只有一个 topic，例如 GA360 的 `GA_SESSIONS_YYYYMMDD`，只保留 `source_collection` 和 `table_group`，避免重复 topic。

## 本轮试错记录

1. 直接跑正式 `db_table_group` 读取 FEC 图会卡住。
   原因是 `_load_tables()` 使用 `workspace.cypher()`，读查询会触发 Snowflake source module 发布；对 FEC 这种 486 表、71,832 列的库，live metadata refresh 成本太高。

2. 验证脚本不能用 `Workspace.clear_graph()` 清 FEC 大图。
   当前实现是单事务 `MATCH (n {project}) DETACH DELETE n`，残留 14,308 个节点时已经触发 Neo4j `dbms.memory.transaction.total.max` 64MB 上限。
   验证脚本改成每批 1000 个节点删除。

3. 一次性导入本地 metadata 时不要用 `MERGE` 写所有 table/col。
   第一次用 `MERGE` 写 7 万列节点，90 秒只到 60 张表。
   清图后用 `CREATE` 写基础节点，并给 `db/schema/table/col/table_group/topic_group._ref` 建索引，完整 FEC 可以在可接受时间内写完。

4. 表组不是业务主题 ontology。
   `table_group` 解决物理分片；`topic_family` 才解决“agent 应该先理解哪个业务概念”。
   FEC 需要两层：先 source，再 topic，再 table_group。

5. 只有 group 节点还不够。
   新 agent 进入数据库时不应该自己猜查询顺序，也不应该先看几百张 table。
   因此增加 deterministic `explorer/schema_landscape.py`：
   - 读取现有 `source_collection/topic_family/table_group`。
   - 写入 `:schema_landscape:knowledge` 节点。
   - 用 markdown `detail` 提供第一屏导航。
   - 通过 `RELATED_TO` 连到 db 和主要 group 节点。
   - 不调用 LLM，不触发 source refresh。

6. table_group 规则必须只覆盖物理分片，不覆盖业务模块。
   第二轮全量检查 152 个 Spider2-Snow DB 时发现两个明显漏识别：
   - `GHCND_1763` 到 `GHCND_2024` 这类历史年份表，旧规则只识别 `19xx/20xx`，漏掉 `17xx/18xx`。
   - `PLACES_TEXAS`、`PLACES_NORTH_CAROLINA`、`CENSUS_TRACTS_AMERICAN_SAMOA` 这类州/领地后缀表，旧规则没有 region shard。

   已修正：
   - 年份 token 从 `(19|20)\\d{2}` 放宽到 `(17|18|19|20)\\d{2}`。
   - 增加美国州/领地后缀规则：`*_TEXAS`、`*_NEW_YORK`、`*_PUERTO_RICO` 等归一为 `*_GEO_REGION`，pattern type 为 `geo_region_shard`。

   修正后的全量统计：

   ```text
   databases: 152
   tables: 7860
   old grouped tables: 4756
   new grouped tables: 5195
   old logical units after table_group: 3481
   new logical units after table_group: 3045
   dbs with logical units <= 50: 137 -> 141
   ```

   典型改善：

   ```text
   GHCN_D: 266 tables, logical units 142 -> 5
   NEW_YORK_GHCN: 288 tables, logical units 152 -> 15
   GEO_OPENSTREETMAP_CENSUS_PLACES: 67 tables, logical units 67 -> 13
   FEC: 486 tables, logical units 108 -> 54
   CENSUS_BUREAU_ACS_1: 351 tables, logical units 103 -> 49
   ```

   明确不放进 table_group 的例子：

   - `HTAN_2` 里的 `SCRNASEQ_*_CURRENT`、`IMAGING_*_CURRENT`、`CLINICAL_*_CURRENT` 是数据产品/实验类型，不是同一个物理表的 shard。
   - `CPTAC_PDC` 里的 `QUANT_PROTEOME_*_CURRENT`、`QUANT_PHOSPHOPROTEOME_*_CURRENT` 是不同研究/组学数据产品，不应该仅凭前缀合成一个 table_group。
   - `BRAZE_USER_EVENT_DEMO_DATASET` 的 `USERS_MESSAGES_EMAIL_*`、`USERS_CANVAS_*` 更适合 topic/module grouping，不是 table_group。
   - `WIDE_WORLD_IMPORTERS` 的 `SALES_*`、`WAREHOUSE_*`、`APPLICATION_*` 是业务模块，应该交给 topic/explorer。

   结论：`table_group` 只负责物理分片/版本/地理 shard；业务主题、模块、数据产品集合交给 `topic_family` 或 explorer review。

7. 第三轮全库审计补了 chromosome/release 两类高置信漏识别。
   全量检查 152 个 Spider2-Snow DB 时，`TCGA`/`TCGA_MITELMAN` 暴露出一个 regex 边界问题：
   - `DNA_METHYLATION_CHR10_HG38_GDC_2017_01` 旧规则没有把 `CHR10` 归一为 `CHR#`，因为 `_` 是 word char，`\b` 在 `10_` 之间不匹配。
   - `TCGA_BIOCLIN_V0` 的 `REL12_CASEDATA`、`REL24_CASEDATA` 是 release shard，旧规则只识别 `_R12`，没有识别开头的 `REL12_`。

   已修正：
   - chromosome shard 从 `\b` 改为 `(?=_|$)`，覆盖 `CHR1_HG38`、`CHRX_HG19`、`__CHR1`。
   - release shard 增加 `^REL\d+(?=_|$)`，覆盖 `REL12_*` 到 `REL24_*`。

   修正后的全量统计：

   ```text
   databases: 152
   tables: 7860
   table groups: 380 -> 392
   grouped tables: 5195 -> 5361
   logical units after table_group: 3045 -> 2891
   dbs with logical units <= 50: 141 -> 142
   ```

   典型改善：

   ```text
   TCGA_BIOCLIN_V0: 80 tables, logical units 80 -> 20
   TCGA: 157 tables, logical units 103 -> 57
   TCGA_MITELMAN: 176 tables, logical units 122 -> 76
   ```

   全 152 个库的逐库审计见 `docs/spider2_table_group_all_db_audit.md`。当前剩余最大 outlier 是 `EBI_CHEMBL`、`HTAN_1`、`HTAN_2`、`CPTAC_PDC`、`TCGA_MITELMAN`；其中大部分剩余负担不是物理分片，而是 source/topic/data-product 层级，应该由 `db_topic_group` 或 explorer 继续压缩。

## 当前限制和下一步

当前 extractor 只做 deterministic physical grouping，暂不调用 LLM。优点是便宜、可复现、可大规模跑；限制是：

- 无法理解没有规律命名的业务主题。
- 无法识别同一业务主题下的不同实体表。
- `numeric_suffix_shard` 可能过宽，需要结合列相似度和文档再确认。
- FEC 这种多来源混合库需要第二层 topic/source grouping。

下一步建议：

1. 让后续 summary/explorer 优先消费 `:schema_landscape`、`:source_collection`、`:topic_family`、`:table_group`。
2. 基于 `table_group` 和 `topic_group` 生成 representative schema prompt section。
3. 对 topic grouping 加入更多 benchmark 数据库的领域 alias，避免只优化 FEC。
