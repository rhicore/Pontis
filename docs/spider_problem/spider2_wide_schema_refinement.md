# Spider2-Snow Wide-Schema Refinement

This document records the current interpretation of very wide Spider2 schemas.
It separates implemented compression from future tidy-data refinement.

## Why Some Databases Have So Many Columns

The largest schemas combine two independent expansion axes:

1. repeated physical tables for years, releases, regions, sources, or shards;
2. wide analytical tables that encode metrics, buckets, periods, or categories
   in column names.

FEC is the main example. Its large column count is not tens of thousands of
independent business keys. It contains many repeated source/year table families
and wide statistical products. Table-family repetition multiplies the number of
physical copies of each wide schema.

A wide table may encode dimensions in headers:

```text
geo_id | male_55_to_59 | female_55_to_59 | income_50000_59999
```

The metric columns share a conceptual structure, but they are still distinct
physical columns and may require different SQL expressions.

## What Current Compression Solves

### Table groups

`db_table_group` combines repeated physical tables in the same official schema.
It records representative members, common columns, variable columns, and the
partition/version pattern.

### Logical columns

For each table group, the extractor creates one `logical_col` per shared column
role and connects it to the corresponding physical columns in all member tables.
This removes repeated shard copies from domain discovery while preserving a
bounded, representative sample of their values.

### Topics

`agent_topic_group` groups table groups and standalone tables by semantic area.
Topics reduce table-routing load, but they do not compress the columns within a
single wide table.

### Column domains

`db_column_domain` clusters key-like logical or standalone columns using semantic
compatibility and value overlap. Measure and payload columns are normally not
useful join-domain candidates.

## What Remains Unsolved

Table groups cannot reduce hundreds of different metrics inside one physical
wide table. Logical columns also do not merge different roles such as
`male_55_to_59` and `female_55_to_59`; they only merge the same role across
repeated tables.

The current Spider pipeline does **not** create `column_group` entities for
within-table metric families. The label remains supported by generic graph
policy for compatibility, but it is not an active Spider extractor output.

This distinction matters:

- `logical_col`: implemented, deterministic, same role across table-group
  members;
- `column_domain`: implemented, value-based candidate domain across logical or
  standalone columns;
- `column_group`: possible future semantic index over different columns inside
  a wide table.

## Why Static Column Grouping Is Risky

Column names and SQL types can identify obvious patterns, but they often cannot
recover the full metric dimensions or units. Similar prefixes may mix counts,
percentages, denominators, confidence intervals, or different populations.

A safe refinement therefore needs two stages:

1. deterministic candidate extraction from names, types, official descriptions,
   ordinals, and repeated signatures;
2. agent review that verifies measure, dimensions, unit, population, and table
   grain before publishing a semantic group.

Until this exists, do not hide physical columns or imply that one group detail
is enough to generate SQL. Use topic/table detail to select a bounded table and
delegate column-level inspection to a child agent.

## Tidy-Data Boundary

Pivoting or unpivoting a wide table can create a cleaner semantic interface, but
it changes the query surface. Pontis currently models the official Snowflake
objects and does not replace them with refined views during benchmark solving.

Possible future refinement views must:

- remain traceable to official physical columns;
- preserve values, null behavior, units, and row grain;
- be generated without reading hidden benchmark answers;
- expose a deterministic SQL rewrite back to official objects.

The broader tidy-data cases and automation boundary are documented in
[Spider2 Tidy Data and Refinement Problem](spider2_tidy_refinement_problem.md).

## Current Decision

Use table groups, logical columns, topics, and column domains now. Treat
within-table metric grouping and pivot/unpivot views as a separate refinement
project, not as part of the current schema extractor.
