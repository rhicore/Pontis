# Storage Architecture

This document describes the current storage shape of Pontis at a high level.

It is intentionally short. Detailed refactor planning lives in:

- [storage_flat_source_modules_plan.md](storage_flat_source_modules_plan.md)
- [neo4j_persistence_refactor_plan.md](neo4j_persistence_refactor_plan.md)

## Purpose

The storage layer gives Pontis one graph-native workspace over heterogeneous data projects.

Its job is to:

- persist graph entities and edges in `.pontis/store.db`
- expose a Cypher read/write surface through `Workspace`
- merge persisted graph data with live source-derived virtual entities
- materialize touched virtual entities only when writes need them

Its job is not to become a general-purpose execution engine for every source type.

## Main Pieces

```text
storage/
├── workspace.py        # Top-level workspace and project routing
├── store.py            # Persistent graph facade over the backend
├── cypher.py           # Cypher subset parser and executor
├── merged.py           # Read view that combines persistent + virtual graph
├── config.py           # Project/source configuration
├── src.py              # Source handle / binding helpers
├── backends/
│   └── sqlite.py       # SQLite persistence backend
└── stores/
    ├── base.py         # Source module protocol
    ├── fs.py           # Filesystem virtual graph and light metadata
    ├── csv_schema.py   # CSV/TSV column projection
    ├── db_schema.py    # SQLite schema projection
    └── text.py         # Shared text-file detection helpers
```

## Read Path

For reads, `Workspace` serves a merged graph view:

1. load persisted nodes and edges from `.pontis/store.db`
2. ask source modules for virtual nodes, edges, properties, and `src` handles
3. merge both into one read-only graph view
4. run Cypher over that merged view

This allows the system to expose file trees, CSV columns, and SQLite schema without eagerly writing all of them into the persistent graph.

## Write Path

For writes, the persistent graph remains the source of truth.

The write flow is:

1. identify any virtual entities touched by the write
2. materialize the necessary closure into the persistent graph
3. apply the write against persisted entities

The storage layer therefore supports a mixed mode:

- virtual graph for cheap discovery and browsing
- persistent graph for extracted knowledge, user edits, and durable relationships

## Public Boundary

The intended public graph API is:

- `Workspace.cypher(...)`

Everything else inside `storage/` is implementation detail or transitional compatibility surface.

Source access is allowed, but it is secondary to graph access:

- source modules may expose native `src` ports
- tools and extractors may use those bindings to open files or database connections
- source-specific execution logic should stay above storage when possible

## Source Modules

`storage/stores/*` is the bridge from raw sources into graph form.

Today those modules mainly cover:

- filesystem discovery
- lightweight text / serialized-file metadata
- CSV/TSV column projection
- SQLite table / column / foreign-key projection

The current refactor direction is to make these modules flatter and more independent, with less peer coupling and less direct access to `Store` internals.

## Current Direction

The storage layer is in a deliberate transition toward:

- a harder `Workspace.cypher(...)` boundary
- flatter source modules under `storage/stores/`
- clearer separation between source projection and extractor-derived knowledge
- more predictable virtual-to-persistent materialization

If a document proposes broader APIs that are not reflected in the current code path, treat it as historical unless it has been folded into the active flat-source-modules plan.
