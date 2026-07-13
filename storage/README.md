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

Project isolation is configured through `graph.uri`, `graph.database`, and the
logical project selected by `Workspace`:

- Neo4j Community/local mode uses one Neo4j process per project. Each project
  has a distinct `graph.uri` and uses `database: neo4j`.
- Neo4j Enterprise/single-process mode can use one shared `graph.uri` and
  distinct `graph.database` values.
- Shared Neo4j Community mode can use one shared `graph.uri` and
  `database: neo4j` for multiple logical projects. In this mode, `Workspace`
  scopes external Cypher to the active project and storage tags published nodes
  with the reserved `project` property.

Spider2-Snow uses shared Neo4j Community mode: all database projects point at
one local Bolt endpoint and are isolated by the reserved `project` property.
BIRD development projects still use one local Neo4j process per project.

When a `Workspace` is created with multiple `active_projects`, those projects
form the maximum visible domain. External Cypher may still filter on
`project`, but the selected projects are intersected with that active domain;
queries cannot see or mutate projects that were not activated in the
`Workspace`.

The old SQLite storage backend, merged read overlay, and in-process Cypher
executor have been removed from the active storage path. `storage/query_inspector.py`
only parses enough Cypher structure for source-module query inspection.

## Layer Boundaries

- `storage/workspace.py` owns project routing, applies user-query project
  scoping, and asks source modules whether they must publish virtual subgraphs
  before a query through `TriggerRouter`. It should not hard-code source labels
  or source-type rules.
- `storage/store.py` owns module publication order. It executes module-declared
  Cypher submissions before the query is sent to Neo4j, then tags published
  nodes with the active logical project.
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

`project` is a reserved storage property. Source modules should not set or
remove it themselves; `Store` stamps published nodes with the active logical
project, and `Workspace.cypher(...)` rewrites external Cypher so node patterns
can only see that project. Tool syntax such as `bird::*:knowledge` remains a
query-level project selector, not an entity-authored metadata field.

Other fields such as `name`, `path`, `ref`, `row_count`, `brief`, and `detail`
are normal properties. Storage must not treat them as universal identity
fields. Counts or endpoint lists that can be derived from graph edges (for
example a table's column count) should be computed from those edges instead
of persisted again as entity metadata.

## Source Modules

Current source modules are:

- `FSModule`: exposes directories, files, suffix-derived file labels, basic
  filesystem metadata, and file/database `src` handles.
- `TextModule`: exposes text-compatible files as `text` and contributes text
  metadata such as encoding, line count, and char count. It can merge onto the
  same physical file node as `FSModule`.
- `SQLiteSchemaModule`: exposes SQLite database tables, views, columns, and
  foreign-key relationship nodes.
- `SnowflakeSchemaModule`: exposes a live Snowflake database as `db`, `schema`,
  `table`/`view`, `col`, and `fk`-compatible graph facts.
- `PostgreSQLSchemaModule`: exposes a live PostgreSQL database the same way.
  Docker-hosted databases are configured as normal PostgreSQL host/port
  endpoints; the source module does not call the Docker API.

Each project has exactly one internal navigation anchor, without a public
`source` label. An `fs` project anchors at its root `dir`; a `sqlite`,
PostgreSQL, or Snowflake project anchors at its `db`. Agent-facing refs are rebuilt
from real graph paths beginning at that anchor and never expose `_ref`.

Every source module is constructed with `ModuleContext` and should only import
`storage.stores.base` from storage internals. It must not inspect `Store`
private fields or import peer modules.

For `source.type: fs`, the registered module chain is:

```text
FSModule -> TextModule -> SQLiteSchemaModule
```

For `source.type: sqlite`, `source.path` points directly to one SQLite file and
the only registered module is `SQLiteSchemaModule`. The database node is the
project's sole navigation anchor; sibling files are not published.

CSV/TSV files remain queryable file data sources, but Pontis does not project
their headers into `csv_table` or `col` graph nodes. The retired implementation
is kept under `storage/stores/useless/`.

For any local PostgreSQL database, use the generic `source.type: postgresql`.
The database may be a normal local PostgreSQL cluster, a port forwarded service,
or a Docker-published endpoint; Pontis only uses host/port credentials.

```yaml
postgresql_source_defaults: &postgresql_source
  type: postgresql
  host: 127.0.0.1
  port: 55432
  user: root
  password: "123123"

projects:
  solar_panel:
    source:
      <<: *postgresql_source
      database: solar_panel
```

When Pontis itself runs inside the same Docker Compose network as
`bird_interact_postgresql_full`, use the service name and container port:

```yaml
source:
  type: postgresql
  host: bird_interact_postgresql_full
  port: 5432
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
