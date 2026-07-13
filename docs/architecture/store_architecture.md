# Storage Architecture

This document describes the current storage shape of Pontis at a high level.

## Purpose

The storage layer gives Pontis one graph-native workspace over heterogeneous
data projects.

Its job is to:

- expose a Cypher surface through `Workspace.cypher(...)`
- publish source-derived subgraphs into Neo4j before matching queries
- execute source-module Cypher submissions before matching queries
- resolve returned pointer strings such as `<pontis:fs:src:README.md>` into
  runtime Python objects after Neo4j returns rows

Its job is not to implement a second general-purpose graph engine beside
Neo4j.

## Main Pieces

```text
storage/
├── workspace.py          # Project routing, query inspection, result pointer resolution
├── store.py              # Source-module trigger execution
├── neo4j/
│   ├── graph.py          # Neo4j connection, node/edge writes, native Cypher execution
│   └── instances.py      # Local multi-process Neo4j runtime management
├── query_inspector.py    # Lightweight Cypher structure parser for module triggers
├── config.py             # Project/source/Neo4j configuration
└── stores/
    ├── base.py           # Source module protocol and ModuleContext
    ├── fs.py             # Filesystem source module
    ├── text.py           # Text metadata source module
    ├── db_schema.py      # SQLite schema source module
    ├── useless/          # Retired source projections, including csv_schema
    └── utils/            # Raw source access helpers used by source modules
```

## Query Path

`Workspace.cypher(...)` is the only intended public graph API.

```text
Workspace.cypher(query)
  -> inspect query labels/properties
  -> ask each source module whether it must publish
  -> execute selected modules' Cypher submissions in Neo4j
  -> execute the original Cypher in Neo4j
  -> replace returned resolver pointers with Python objects
```

Neo4j is therefore the only durable graph store and the only full Cypher
executor. Source modules only produce subgraphs and runtime pointer values.

## Source-rooted refs

Every project publishes one internal navigation anchor without adding a public
label. An `fs` project anchors at `.:dir`; a `sqlite` or direct database project
anchors at `<database>:db`. Public `find` and `meta` refs are reconstructed from the
shortest real graph path beginning there. `_ref`, `path`, entity ids, and the
internal `_source_anchor` property are not part of the public display contract.

## Source Modules

`storage/stores/*` modules are intentionally flat:

- each module imports `storage.stores.base`, not peer modules
- each module receives `ModuleContext`, not `Store`
- source access goes through `ctx.source`
- each module owns its own Cypher `MATCH` / `MERGE` / `DELETE` submission logic
- Store only executes module submissions in registration order

For `source.type: fs`, the registered module chain is:

```text
FSModule -> TextModule -> SQLiteSchemaModule
```

The chain is registration order, not module dependency order.

CSV/TSV files may still be queried as file sources. Their headers are not
projected into `csv_table`/`col`; the legacy projection is under
`storage/stores/useless/csv_schema.py`.
