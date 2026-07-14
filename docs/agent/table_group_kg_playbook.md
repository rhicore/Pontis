# Spider2 Knowledge-Graph Navigation Playbook

This is the current traversal contract for large Spider2-Snow projects. The
physical database structure remains intact; navigation entities add alternative
graph paths and never replace official tables or columns.

## Graph Layers

```text
db
├── schema
│   ├── table / view
│   │   └── col
│   ├── table_group ── member table
│   │   └── logical_col ── member col
│   └── topic ── table_group / standalone table
├── column_domain ── logical_col / standalone col
└── schema_landscape
```

The layers have different authority:

| Layer | Created by | Meaning |
|---|---|---|
| `schema/table/view/col` | Official Spider resources | Physical Snowflake structure |
| `table_group/logical_col` | Deterministic extractor | Repeated physical shards and their shared column roles |
| `topic` | Explorer agent | Semantic routing over table groups and standalone tables |
| `column_domain` | Extractor, then review agent | Candidate shared value domain; not automatically a join relation |
| `schema_landscape` | Deterministic explorer | First-screen index over the current navigation graph |

Legacy `source_collection` and `topic_family` nodes may exist in old graphs,
but the current Spider pipeline does not create them. New logic must use
official `schema` plus agent-authored `topic`.

## Preprocessing Order

The authoritative order is defined in
`scripts/spider/extract_spider2_snow.py`:

1. `spider2_snow_schema`
2. `db_table_group`
3. `db_column_stats`
4. `db_column_domain`
5. `agent_topic_group`
6. `agent_spider_navigation_prepare`
7. `agent_column_domain_review`
8. `schema_landscape`
9. `semantic_embedding`

`agent_spider_navigation_prepare` writes `brief/detail` only for schemas,
topics, table groups, and standalone tables. Member tables and ordinary columns
remain available but are not mandatory description targets.

## Agent Read Order

### 1. Start from the landscape

```text
find({"ref":"*:schema_landscape"})
meta({"ref":"<landscape ref>","property":["brief","detail"]})
```

The landscape is an index, not schema evidence. Use it to select a schema,
topic, or table group; do not write SQL from the landscape alone.

### 2. Select an official schema

```text
find({"ref":"*:schema"})
meta({"ref":"<schema ref>","property":["brief","detail"]})
```

Schema is Snowflake's namespace boundary. It is independent of semantic topic
and physical table-family grouping.

### 3. Route through topics

A topic connects the relevant table groups and standalone tables in one schema.
Read topic detail to narrow the search and to decide whether a child agent
should inspect a bounded subset.

Do not treat topic detail as a replacement for table detail. A standalone table
must still be read before using its columns.

### 4. Expand table groups conservatively

Read these fields first:

- `family`, `pattern_types`, and `consistency`;
- `member_count` and `representative_members`;
- `common_columns` and `variable_columns`;
- `agent_usage_hint`, `brief`, and `detail`.

For `same_order` or `same_set`, one representative member usually explains the
shared schema. For `drifting`, inspect `variable_columns` and the specific
member selected by date, release, region, chromosome, or suffix.

### 5. Use logical columns for repeated roles

`logical_col` combines the same column role across all members of one table
group. Its statistics use bounded samples from the physical member columns so
domain discovery remains tractable. Use it for retrieval and domain reasoning,
then resolve the selected role back to the correct physical member before
generating SQL.

### 6. Treat value domains as reviewed candidates

`column_domain` means that its members have sufficient value evidence to be
clustered. It does not imply that every member pair is a valid foreign key or
that a join preserves row grain.

- `accepted`: useful shared-domain evidence, still verify join direction and
  grain.
- `needs_split`: mixed domain; do not infer a join from membership alone.
- `rejected`: do not use as join evidence.
- `pending_review`: candidate only.

Prefer explicit `fk` and reviewed `rel` entities over value-domain inference.

## Grouped and Standalone Labels

Graph policy maintains navigation labels automatically:

- `table:grouped`: connected to a `table_group`;
- `table:standalone`: not connected to a `table_group`;
- `col:grouped`: represented by a `logical_col` or legacy `column_group`;
- `col:standalone`: not represented by either.

Always combine the navigation label with the physical label. `standalone`
alone is not an entity type.

The official `schema -> table` edge remains present even for grouped tables.
Grouping is an additional graph view, not a destructive tree rewrite.

## Child-Agent Boundary

Use a child agent when a selected topic, table group, or standalone table still
contains too many columns for one context. Pass:

- the original instruction and relevant external-knowledge excerpt;
- explicit topic/table/table-group refs;
- the required output: candidate physical tables, columns, row grain, filters,
  aggregations, and join evidence.

The child agent performs bounded read-only analysis. The main agent validates
the report, chooses physical table members, and writes the final SQL.

## Invariants

- Keep physical ownership in graph edges; do not duplicate schema or table names
  into child metadata merely for navigation.
- A physical column belongs to exactly one table or view.
- A physical column belongs to at most one `logical_col`.
- A `logical_col` belongs to exactly one `table_group`.
- A topic may overlap another topic, but only when the same unit genuinely has
  multiple semantic roles.
- A table group never crosses an official schema.
