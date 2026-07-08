# Table Group KG Playbook

这份手册给新 agent 使用。目标是在进入一个 Pontis database graph 后，不逐表扫描，而是先通过自动生成的 source/topic/table group 节点建立 schema 认知。

## 读取顺序

推荐顺序固定为三层：

1. `:source_collection`：数据库是否混合多个数据来源。
2. `:topic_family`：每个 source/schema 下面有哪些业务主题或数据产品。
3. `:table_group`：主题里哪些物理表其实是时间、版本、shard 或 release family。

只有问题明确命中特定日期、年份、版本、suffix、列名或实体时，才下钻到单张 `:table` 和 `:col`。

## Landscape 层

新 agent 进入大型数据库时，第一步先找数据库级导航节点：

```cypher
MATCH (l:schema_landscape)
RETURN l.name AS name,
       l.brief AS brief,
       l.table_count AS tables,
       l.column_count AS columns,
       l.source_collection_count AS sources,
       l.topic_family_count AS topics,
       l.table_group_count AS table_groups,
       l.schema_reading_strategy AS strategy,
       l.detail AS detail
ORDER BY tables DESC
```

解释：

- `schema_landscape.detail` 是第一屏 markdown，总结 source、topic 和主要 table group。
- 它不替代具体节点；它只告诉 agent 应该先看哪里。
- 如果存在 `schema_landscape`，不要先展开所有 `table/col`。

从 landscape 找相关导航节点：

```cypher
MATCH (l:schema_landscape)<-[:RELATED_TO]-(n)
WHERE n:source_collection OR n:topic_family OR n:table_group
RETURN DISTINCT labels(n) AS labels, n.name AS name, n._ref AS ref
LIMIT 100
```

## Source 层

先查 source/schema 边界：

```cypher
MATCH (g:source_collection)
RETURN g.schema_name AS schema,
       g.physical_table_count AS physical_tables,
       g.logical_unit_count AS logical_units,
       g.table_group_count AS table_groups,
       g.standalone_table_count AS standalone_tables,
       g.dominant_topics AS dominant_topics,
       g.schema_reading_strategy AS strategy
ORDER BY physical_tables DESC
```

解释：

- `physical_table_count` 大，说明 source 本身很大。
- `logical_unit_count` 小于 `physical_table_count` 很多，说明下层 table_group 已经压缩了大量物理表。
- `dominant_topics` 是 source 内最主要的 topic key。
- 如果只有一个 source，例如 GA360，也仍然有价值：它告诉 agent 整个库基本是一个来源，不需要先做 source disambiguation。

## Topic 层

再查 source 内主题：

```cypher
MATCH (g:topic_family)
RETURN g.schema_name AS schema,
       g.topic_key AS topic,
       g.topic_label AS label,
       g.physical_table_count AS physical_tables,
       g.logical_unit_count AS logical_units,
       g.table_group_count AS table_groups,
       g.standalone_table_count AS standalone_tables,
       g.sample_tables AS sample_tables,
       g.schema_reading_strategy AS strategy
ORDER BY physical_tables DESC, schema, topic
```

解释：

- `topic_key` 是稳定机器键，适合后续检索。
- `topic_label` 是给 agent 读的短说明。
- `logical_units` 里 table_group 算 1 个单元，因此它更接近“agent 需要理解多少个概念”。
- `sample_tables` 只用于快速识别命名风格，不要把它当完整成员列表。
- 在多主题 schema 里，即使一个 topic 只有 1 个 logical unit，只要它覆盖多张物理表，也会生成 `topic_family`。例如 FEC 的 `committee_master` 对应一个 `CMYY` table_group，但仍然是一个应该优先展示的业务主题。

FEC 里应优先看到这类 topic：

```text
individual_contributions
committee_master
candidate_master
pas2_contributions
operating_expenditures
other_committee_transactions
candidate_committee_linkages
```

## Table Group 层

最后查物理表组：

```cypher
MATCH (g:table_group)
RETURN g.schema_name AS schema,
       g.family AS family,
       g.member_count AS members,
       g.primary_pattern_type AS pattern,
       g.cognitive_shape AS shape,
       g.consistency AS consistency,
       g.representative_members AS representative_members,
       g.common_columns AS common_columns,
       g.variable_columns AS variable_columns,
       g.schema_reading_strategy AS strategy,
       g.agent_usage_hint AS hint
ORDER BY members DESC, schema, family
```

解释：

- `cognitive_shape=time_partitioned_table_family`：按时间/周期分区理解。
- `cognitive_shape=versioned_snapshot_family`：先确定版本或 release。
- `cognitive_shape=domain_sharded_table_family`：按领域 shard 选择成员。
- `cognitive_shape=enumerated_shard_family`：谨慎，需要结合文档或样例确认 suffix 语义。
- `consistency=same_order`：一张代表表基本能代表全组。
- `consistency=same_set`：列集合稳定，但顺序可能不同。
- `consistency=drifting`：只能把 `common_columns` 当稳定 schema，`variable_columns` 需要命中特定成员后再用。

## 下钻关系

从 source/topic/table group 找成员：

```cypher
MATCH (g:source_collection)<-[:RELATED_TO]-(n)
WHERE n:topic_family OR n:table_group OR n:table
RETURN DISTINCT g.schema_name AS source, labels(n) AS labels, n.name AS name, n._ref AS ref
LIMIT 100
```

```cypher
MATCH (g:topic_family {topic_key: $topic})<-[:RELATED_TO]-(n)
WHERE n:table_group OR n:table
RETURN DISTINCT labels(n) AS labels, n.name AS name, n._ref AS ref,
       n.table_group_family AS table_group_family
LIMIT 100
```

```cypher
MATCH (g:table_group {family: $family})<-[:RELATED_TO]-(t)
WHERE t.table_name IS NOT NULL
RETURN t.schema_name AS schema, t.table_name AS table, t._ref AS ref
ORDER BY table
```

## Prompt 压缩策略

把 KG 摘要放进 prompt 时，建议按这个规则：

- source 层：只放 `schema_name`、`physical_table_count`、`logical_unit_count`、`dominant_topics`。
- topic 层：只放命中的 `topic_key/topic_label` 和 `logical_units` 摘要。
- same-order table_group：放 1 个 `representative_member` 的 DDL。
- same-set table_group：放 1 个代表表 DDL，并说明列顺序不构成语义。
- drifting table_group：放 `common_columns`、`variable_columns`、`representative_members`。
- compact year suffix 表组，例如 `INDIVYY`、`CMYY`、`PAS2YY`：先解释 suffix 是年份/选举周期，再选择成员。

## 字段存储注意事项

Neo4j property 只能存 primitive 或 primitive list。当前 extractor 会这样处理：

- `common_columns`、`variable_columns`、`representative_members`、`sample_tables` 是字符串列表，可直接读。
- `representative_column_signatures`、`logical_units` 是结构化列表，会通过 JSON 字符串存储。
- `table_refs`、`table_group_refs` 可能被截断；如果需要完整成员，应走关系边查询，不要只读属性。

## 什么时候继续下钻

只有以下情况才应该展开单表：

- 用户问题提到明确日期、年份、release、版本、州、地区、suffix。
- `variable_columns` 包含问题所需字段。
- topic/source 里有多个候选 table_group，无法只靠 topic 判断。
- SQL 需要具体物理表名，而不是逻辑 family 名。

否则，优先停留在 source/topic/table_group 级别，避免把几百张物理表重新塞进上下文。
