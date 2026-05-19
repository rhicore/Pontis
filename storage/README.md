# storage architecture notes

`storage` is the graph boundary of Pontis. Its public entrypoint is
`Workspace.cypher(...)`; external tool, agent, extractor, and benchmark code
should not call `workspace._*` or `store._*` methods directly.

## Current Direction

The storage execution path is Neo4j-only:

```text
Workspace.cypher(query)
  -> inspect query
  -> build query TriggerEvent
  -> TriggerRouter selects source modules
  -> source modules return CypherStatement submissions
  -> Store executes those submissions in Neo4j
  -> execute the original Cypher in Neo4j
```

Neo4j is the formal query surface for durable graph data, indexes, constraints,
full Cypher, and future semantic/vector search.

Project isolation is configured through `graph.uri` and `graph.database`:

- Neo4j Community/local mode uses one Neo4j process per project. Each project
  has a distinct `graph.uri` and uses `database: neo4j`.
- Neo4j Enterprise/single-process mode can use one shared `graph.uri` and
  distinct `graph.database` values.

In both modes, `project` is a `Workspace.cypher(..., project=...)` route, not a
node property.

The old SQLite storage backend, merged read overlay, and in-process Cypher
executor have been removed from the active storage path. `storage/query_inspector.py`
only parses enough Cypher structure for source-module query inspection.

## Layer Boundaries

- `storage/workspace.py` owns project routing and asks source modules whether
  they must publish virtual subgraphs before a query through `TriggerRouter`.
  It should not hard-code source labels or source-type rules.
- `storage/store.py` owns module publication order. It executes module-declared
  Cypher submissions before the query is sent to Neo4j.
- `storage/neo4j/graph.py` owns Neo4j connection management, durable node/edge writes,
  native Cypher execution, and Neo4j result normalization.
- `storage/neo4j/instances.py` owns local one-project-one-Neo4j-process
  management for Neo4j Community deployments.
- `storage/triggers.py` owns the minimal trigger event and router used by
  query-time source refresh.
- `storage/stores/` owns source modules. A module can decide query triggers,
  generate Cypher submissions, write resolver pointer properties, and resolve
  returned pointers.

## Node Contract

All graph nodes share the same public shape:

- `id`: internal Pontis surrogate id, created or loaded by storage.
- `labels`: graph labels used by Cypher label matching.
- `src`: optional resolver pointer string, such as
  `<pontis:project:fs:src:README.md>`, which Neo4j treats as a normal string
  and Workspace may replace after query execution.

`project` is not a node property. It is a `Workspace.cypher(..., project=...)`
route that selects which project graph database receives the query. Tool syntax
such as `bird::*:knowledge` is therefore a query-level project selector, not an
entity filter.

Other fields such as `name`, `path`, `ref`, `row_count`, `column_count`,
`brief`, and `detail` are normal properties. Storage must not treat them as
universal identity fields.

## Source Modules

Current source modules are:

- `FSModule`: exposes directories, files, suffix-derived file labels, basic
  filesystem metadata, and file/database `src` handles.
- `TextModule`: exposes text-compatible files as `text` and contributes text
  metadata such as encoding, line count, and char count. It can merge onto the
  same physical file node as `FSModule` or `CSVSchemaModule`.
- `CSVSchemaModule`: exposes CSV/TSV columns as `col` virtual nodes.
- `SQLiteSchemaModule`: exposes SQLite database tables, views, columns, and
  foreign-key relationship nodes.

Every source module is constructed with `ModuleContext` and should only import
`storage.stores.base` from storage internals. It must not inspect `Store`
private fields or import peer modules.

For `source.type: fs`, the registered module chain is:

```text
FSModule -> TextModule -> CSVSchemaModule -> SQLiteSchemaModule
```

## Module Submission Rule

Modules do not call the Neo4j driver directly. Each module returns one or more
Cypher statements:

```python
StoreModule.cypher_statements() -> list[CypherStatement]
```

The module owns its own `MATCH` / `MERGE` / `DELETE` semantics. `Store` only
executes the statements in order. This keeps complex source-specific behavior
inside the module, for example foreign keys can be merged by matching related
table/column nodes instead of by a single universal URI.

```text
FS/Text file node -> module MERGEs by {path}
CSV columns       -> module MERGEs by {ref}
SQLite fk         -> module MERGEs by matched from/to column nodes
```

The internal `ent_xxx` id is still required because edges, stable tool results,
and Neo4j constraints need a storage-owned handle. Each module's Cypher is
responsible for assigning it on create.

## Resolver Pointer Rule

`src` and similar runtime objects are not Neo4j execution features. They are
ordinary string properties during Neo4j query execution.

```text
Neo4j value: <pontis:project:module:kind:payload>
Workspace postprocess:
  -> parse project/module/kind
  -> pass payload to module.resolve_pointer(...)
  -> replace the original string with the returned Python object
```

Resolver pointers do not participate in storage identity, query planning, sync
deletion, or semantic indexing.

## Cleanup Notes

Remaining work:

- Source modules currently generate full refresh statements; large sources will
  need query-aware discovery, cached snapshots, or module-owned incremental
  Cypher later.
- Batch node writes, source freshness, stale marking, and source-owned versus
  user-owned property ownership are still future work.
