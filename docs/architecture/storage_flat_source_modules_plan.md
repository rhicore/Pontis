# Storage Flat Source Modules Refactor Plan

## Goal

Make every module under `storage/stores/` flat and independent:

- A source module may import only `storage.stores.base` from Pontis storage internals.
- Source modules must not import other `storage/stores/*` modules or helpers.
- Source modules must not receive or inspect `Store` internals such as `_project_path`, `_backend_db_path`, `_id_index`, `_cypher_internal`, or `_read_edges_storage`.
- Source modules should only implement the base protocol and access the project configuration plus the current data source through an injected context.
- `Workspace.cypher(...)` remains the public graph API.

The target is not merely import cleanup. The current module model lets modules coordinate implicitly through shared path/ref conventions and Store private fields. A flat architecture needs an explicit module context, structured virtual identities, and a central virtual graph composer.

## Current State

The current storage layer has four important parts:

| Area | Current role |
| --- | --- |
| `Workspace` | Loads projects, creates stores, owns module list per project, chooses merged read view for read queries, materializes virtual entities before writes |
| `Store` | Persistent graph facade over backend, indexes nodes/edges, dispatches module hooks, exposes private methods used by Cypher executor |
| `MergedStoreView` | Builds a read-only graph by combining persisted nodes/edges with all module virtual nodes/edges |
| `storage/stores/*` | Source modules and helper files that expose files, CSV columns, SQLite schema, text recognition, DB helpers |

Current module dependencies are not flat:

- `storage/stores/__init__.py` statically imports `FSModule`, `CSVSchemaModule`, and `SQLiteSchemaModule`.
- `fs.py` imports `storage.enricher`, `storage.src.SrcHandle`, `storage.stores.text`, and `storage.stores.utils.db`.
- `db_schema.py` imports `storage.stores.utils.db`.
- `text.py` is a helper, not a `StoreModule`, but is imported by both storage and extractor code.
- `stores/utils/db.py` is a shared peer helper used by multiple modules.

Current module access is also too powerful:

- Modules receive a `store` object and read `store.project_path`, `store._project_path`, and `store._backend_db_path`.
- Modules perform source traversal themselves with `os.walk`.
- Modules bind `src` handles themselves.
- Modules return ad hoc dict nodes and string edge endpoints.

This works while modules are few, but it makes independence hard to enforce.

## Core Design Decision

The refactor should introduce a `ModuleContext`.

Modules should be constructed as:

```python
module = CsvSchemaModule(context)
```

not:

```python
module = CsvSchemaModule(store)
```

`ModuleContext` is the only bridge between a source module and the rest of storage. It should expose:

- `project_name`
- `project_config`
- `source_config`
- `graph_config`
- `source`, a data source adapter for listing, stat, opening, and native ports
- `cache`, a small per-module cache keyed by source fingerprint

The module should not know whether the persistent graph uses SQLite, memory, or a future backend.

## Required New Contracts

`storage/stores/base.py` should become the only internal storage import that source modules need.

It should contain these contracts:

```python
@dataclass(frozen=True)
class SourceKey:
    source: str
    kind: str
    path: str = ""
    entity: str = ""

@dataclass
class VirtualNode:
    key: SourceKey
    labels: list[str]
    props: dict
    source_owned: set[str] = field(default_factory=set)

@dataclass
class VirtualEdge:
    a: SourceKey
    b: SourceKey
    labels: list[str] = field(default_factory=list)

@dataclass
class MatchQuery:
    query: str
    params: dict
    var: str = "n"

class SourceHandle:
    ...

class SourceAdapter(Protocol):
    def walk(self) -> Iterable[SourceEntry]: ...
    def stat(self, path: str) -> SourceStat | None: ...
    def open(self, path: str, *args, **kwargs): ...
    def absolute_path(self, path: str) -> str | None: ...
    def is_backend_artifact(self, path: str) -> bool: ...

class ModuleContext:
    project_name: str
    project_config: ProjectConfig
    source_config: SourceConfig
    graph_config: GraphConfig
    source: SourceAdapter
    cache: ModuleCache

class StoreModule:
    name = "module"
    source_types: set[str] = set()

    def iter_virtual_nodes(self) -> Iterable[VirtualNode]: ...
    def iter_virtual_edges(self, nodes: Iterable[VirtualNode]) -> Iterable[VirtualEdge]: ...
    def get_virtual_meta(self, key: SourceKey | str) -> dict | None: ...
    def get_virtual_neighbors(self, key: SourceKey | str) -> list[SourceKey]: ...
    def bind_src(self, node: dict) -> SourceHandle | None: ...
    def match_query(self, node: dict) -> MatchQuery | None: ...
```

During migration, the old dict/string protocol can remain, but the destination should be structured keys.

## Why Structured Virtual Keys Are Necessary

Flattening modules means multiple independent modules may describe the same physical source node.

Example:

- `fs_tree` sees `people.csv` as a file.
- `csv_schema` sees `people.csv` as a CSV table and emits columns.
- `text_meta` may also recognize it as text-like.

If `MergedStoreView` simply appends all virtual nodes, the same file becomes several separate virtual nodes. The current implementation partially avoids this because `FSModule` owns most file labels, but that coupling is exactly what we want to remove.

The fix is to make the center merge virtual nodes by `SourceKey`, before assigning virtual ids:

```text
SourceKey(source="local", kind="file", path="people.csv")
```

All modules that know something about `people.csv` emit the same key. The composer merges labels and props:

```text
["file"] + ["csv"] + ["text"] -> ["file", "csv", "text"]
```

This is the most important architectural step. Without it, the flat-module refactor will create duplicated graph nodes and unstable traversal behavior.

## Virtual Merge And `match_query`

The virtual-node merge logic should be consistent with `StoreModule.match_query(...)` at the identity-contract level, but it should not literally use the same execution path.

There are two different merge stages:

| Stage | Input | Output | Correct identity mechanism |
| --- | --- | --- | --- |
| virtual-to-virtual merge | virtual nodes from multiple source modules | one merged virtual node | `SourceKey` equality |
| virtual-to-persistent reuse | one virtual node and the persisted graph | zero/one/many persisted candidates | `match_query(...)` |

They must agree on what "the same entity" means. For example, all modules describing the physical file `people.csv` should emit:

```text
SourceKey(source="local", kind="file", path="people.csv")
```

Then `match_query(...)` for that same virtual file should be mechanically consistent with the same identity:

```cypher
MATCH (n:file) WHERE n.source_key = $source_key RETURN n
```

or, during compatibility migration:

```cypher
MATCH (n:file) WHERE n.path = $path RETURN n
```

The key rule is:

```text
SourceKey decides whether virtual nodes from modules are the same virtual entity.
match_query decides whether that virtual entity should reuse an existing persisted entity.
Both are derived from the same identity model.
```

Do not use `match_query(...)` itself to merge virtual nodes across modules. That would be wrong for three reasons:

- `match_query(...)` is a persisted-graph lookup; virtual-to-virtual merge happens before persisted graph reuse.
- It would make read-path virtual composition depend on executing Cypher once per virtual node, which is expensive.
- It cannot safely merge two not-yet-persisted virtual nodes unless both have already been represented in the persisted graph, which defeats the purpose of virtual modules.

The recommended API shape is to make identity explicit:

```python
class StoreModule:
    def identity(self, node: VirtualNode) -> SourceKey:
        return node.key

    def match_query(self, node: VirtualNode | dict) -> MatchQuery | None:
        ...
```

Longer term, `match_query(...)` should usually be generated from `SourceKey` plus module-declared persistence rules, rather than handwritten independently in every module. Handwritten `match_query(...)` should be reserved for genuinely ambiguous or legacy cases.

## If All Matching Must Use `match_query`

If we force every identity decision to use `match_query(...)`, including virtual-to-virtual matching, the architecture must change more substantially.

In that model, `match_query(...)` is no longer "query persisted Store to find reuse candidates". It becomes a universal identity query that can run against any graph-like view:

```text
match_query(vnode) runs against CompositeGraphView
CompositeGraphView = PersistentGraphView + VirtualStagingGraph
```

The flow becomes:

```text
for module in active_modules:
    for vnode in module.iter_virtual_nodes():
        q = module.match_query(vnode)
        matches = match_engine.execute(q, graph=Persistent + AcceptedVirtual)

        if len(matches) == 0:
            add vnode to VirtualStagingGraph
        elif len(matches) == 1:
            merge vnode into that matched node
        else:
            record conflict; do not guess
```

This preserves the current `0/1/N` semantics, but applies them to both virtual and persisted candidates.

### Required Core Piece: `GraphView`

This approach requires extracting the minimum interface used by `CypherExecutor`.

Today `CypherExecutor` reaches into:

- `store._id_index`
- `store._adjacent`
- `store._get_meta(...)`
- `store.bind_src(...)`
- `store._set_meta(...)`
- `store._create_node(...)`
- `store._delete_node(...)`
- `store._add_edges(...)`

For universal query matching, we need a read-only graph interface:

```python
class GraphView(Protocol):
    project_name: str
    internal_fields: set[str]

    def ensure_index(self) -> None: ...
    def iter_node_ids(self) -> Iterable[str]: ...
    def node_props(self, node_id: str) -> dict | None: ...
    def adjacent_ids(self, node_id: str) -> set[str]: ...
    def get_meta(self, node_id: str, include_props=None) -> dict | None: ...
    def bind_src(self, node: dict) -> SourceHandle | None: ...
```

Then we provide:

- `PersistentGraphView(Store)`
- `VirtualStagingGraph`
- `CompositeGraphView(PersistentGraphView, VirtualStagingGraph)`

`CypherExecutor` should read through this interface. Write operations can still target `Store`; identity matching should use read-only views.

### Virtual Staging Graph

`VirtualStagingGraph` is an in-memory graph of virtual nodes already accepted by the match engine.

It stores:

- virtual ids such as `_v_csv_schema_12`
- props
- labels
- module provenance
- virtual edges
- optional source-owned keys

When a later module emits a node whose `match_query(...)` matches an existing virtual id, the center merges into that virtual node.

This is how flat modules can independently emit facts about the same source object without importing each other.

### Identity Query Shape

To make this efficient and safe, identity `match_query(...)` should be restricted to a small query shape:

```cypher
MATCH (n:Label)
WHERE n.source_id = $source_id
  AND n.source_kind = $source_kind
  AND n.source_path = $source_path
  AND n.source_entity = $source_entity
RETURN n
```

The fields can be named differently, but the principle matters:

- equality-only
- deterministic
- no traversal for identity
- no expensive virtual properties
- no semantic matching

Arbitrary Cypher can remain as fallback for legacy modules, but the fast path should compile identity queries into an index lookup.

### SourceKey Still Exists, But As Data

Even in an all-`match_query` design, `SourceKey` is still useful. It just becomes query data instead of the direct merge key.

Example virtual node props:

```python
{
    "source_id": "local",
    "source_kind": "file",
    "source_path": "people.csv",
}
```

The module's match query uses those props:

```python
return MatchQuery(
    query=(
        "MATCH (n:file) "
        "WHERE n.source_id = $source_id "
        "AND n.source_kind = $source_kind "
        "AND n.source_path = $source_path "
        "RETURN n"
    ),
    params={
        "source_id": vnode["source_id"],
        "source_kind": vnode["source_kind"],
        "source_path": vnode["source_path"],
    },
)
```

So the identity model is still explicit, but the center performs matching only by executing a query.

### Edge Resolution Also Uses Query Matching

If the rule is "all matching uses querymatch", virtual edges should not rely on raw string refs either.

Instead of:

```python
VirtualEdge(a="people.csv", b="people.csv--score")
```

prefer:

```python
VirtualEdge(
    a=EndpointQuery(...match file people.csv...),
    b=EndpointQuery(...match column people.csv/score...),
)
```

The composer resolves each endpoint by running the endpoint query against `CompositeGraphView`.

Rules:

- zero endpoint matches: skip or report dangling virtual edge
- one endpoint match: use it
- multiple endpoint matches: conflict, no edge

During migration, modules may still return string endpoints, but the target should be endpoint queries.

### Conflict Rules

The current semantics should stay:

- 0 matches: create a new virtual candidate
- 1 match: merge into the matched candidate
- N matches: do not guess

For conflicts, the match engine should return structured diagnostics:

```python
MatchConflict(
    module="csv_schema",
    node=...,
    query=...,
    candidates=[...],
)
```

Do not silently create another duplicate node when `N > 1`. That would hide identity bugs.

### Ordering

Because virtual-to-virtual matching now depends on previously accepted virtual nodes, module order must be deterministic.

Recommended order for filesystem source:

```text
fs_tree
csv_schema
sqlite_schema
serialized_meta
text_meta
```

This order is not a semantic dependency between modules; it is just deterministic staging order. Since identity matching runs against the accumulated graph, the same source identity should converge to one node as long as every module uses compatible `match_query(...)` rules.

### Performance Requirements

Naively running Cypher for every virtual node against a composite graph will be expensive.

Required mitigations:

- Normalize identity `match_query(...)` into a restricted form.
- Build indexes on common identity props, such as `(source_id, source_kind, source_path, source_entity)`.
- Cache match results by `(query, params, persistent_graph_version, virtual_graph_version)`.
- Keep expensive metadata out of identity queries.
- Treat arbitrary Cypher identity queries as slow-path compatibility.

The all-query design is viable only if identity queries are compiled or indexed. Otherwise a large project will degrade badly.

### Revised Implementation Order For All-Query Matching

If this is the enforced direction, use this order:

1. Extract read-only `GraphView` from `CypherExecutor`.
2. Implement `PersistentGraphView` adapter around `Store`.
3. Implement `VirtualStagingGraph`.
4. Implement `CompositeGraphView`.
5. Implement `MatchEngine` with `0/1/N` semantics.
6. Make `MergedStoreView` delegate virtual node assembly to `MatchEngine`.
7. Add identity props to all virtual nodes.
8. Rewrite each module's `match_query(...)` to match those identity props.
9. Convert virtual edges to endpoint queries.
10. Add dependency and identity-conflict tests.

This path is harder than direct `SourceKey` merging, but it has one advantage: all identity decisions become explainable as queries, and the same semantics are used for virtual modules and persistent graph reuse.

## Target Module Layout

The final `storage/stores/` directory should look like this:

```text
storage/stores/
├── base.py              # contracts only
├── registry.py          # dynamic module loading / registry
├── fs_tree.py           # generic directory and file nodes
├── csv_schema.py        # CSV/TSV file labels, columns, delimiter, light counts
├── sqlite_schema.py     # SQLite file labels, tables, views, columns, physical FK
├── serialized_meta.py   # JSON/YAML/XML/TOML/HCL light structure
└── text_meta.py         # text recognition, encoding, line/char counts
```

Files to remove or absorb:

- `storage/stores/utils/db.py`: move required DB access behind `SourceAdapter` or duplicate tiny SQLite helpers inside `sqlite_schema.py`.
- `storage/stores/text.py`: move text recognition into `text_meta.py` or into `SourceAdapter.classify(...)`.
- `storage/enricher.py`: remove as a separate cross-module property mechanism; light meta should be owned by explicit modules.
- `storage/src.py`: move `SourceHandle` into `storage/stores/base.py` or another core contract module that every source module is allowed to import.

## Allowed And Forbidden Imports

Allowed in a source module:

```python
from storage.stores.base import StoreModule, ModuleContext, VirtualNode, VirtualEdge, MatchQuery, SourceHandle
```

Allowed from outside Pontis:

- Python stdlib
- Optional parser/client dependencies used by that module, such as `csv`, `json`, `sqlite3`, `xml.etree`, `yaml`

Forbidden:

```python
from storage.store import Store
from storage.workspace import Workspace
from storage.src import SrcHandle
from storage.enricher import enrich_meta
from storage.stores.fs import ...
from storage.stores.text import ...
from storage.stores.utils import ...
```

The registry/factory may import modules. Peer modules may not import each other.

## Proposed Data Flow

Read query:

```text
Workspace.cypher
  -> Store persistent graph
  -> ModuleRegistry active modules
  -> VirtualGraphComposer
       - asks each module for virtual nodes/edges
       - merges nodes by SourceKey
       - resolves match_query against persistent graph
  -> MergedStoreView
  -> CypherExecutor
```

Write query:

```text
Workspace.cypher
  -> parse query
  -> MergedStoreView match
  -> materialize matched virtual SourceKeys
  -> write persistent nodes/edges to Store
  -> execute SET / DELETE / CREATE edge
```

The important invariant:

```text
Modules describe source facts.
Workspace/Store decide persistence.
Cypher remains the only public graph interface.
```

## Implementation Phases

### Phase 0: Freeze Existing Behavior

Keep `scripts/storage/test_store.py` as the acceptance script.

Before starting this refactor, it should cover:

- CRUD through Cypher
- fixed and variable traversal
- virtual file entities
- `src` binding
- SQLite schema virtual tables/columns/FKs
- CSV virtual columns
- serialized/text light metadata
- write-path materialization
- persistence and concurrent visibility

No module flattening should proceed without this script passing.

### Phase 1: Introduce `ModuleContext` Without Behavior Change

Add `ModuleContext`, `SourceAdapter`, `SourceHandle`, `VirtualNode`, and `VirtualEdge` to `storage/stores/base.py`.

Then update `stores.create_store(...)`:

```python
context = ModuleContext(...)
store.add_module(ModuleClass(context))
```

For compatibility, modules can temporarily accept either a `Store` or `ModuleContext`, but new modules should only use context.

This phase should not change output.

### Phase 2: Move Source Access Behind `SourceAdapter`

Create an FS source adapter for local filesystem projects.

It should own:

- absolute path resolution
- `.pontis/store.db` backend artifact exclusion
- `walk()`
- `stat(path)`
- `open(path, ...)`
- optional `connect_sqlite(path, readonly=True, immutable=True)`

After this phase, modules stop reading:

- `store.project_path`
- `store._project_path`
- `store._backend_db_path`

They use:

```python
self.ctx.source.walk()
self.ctx.source.stat(path)
self.ctx.source.open(path, ...)
```

### Phase 3: Add `VirtualGraphComposer`

Move virtual graph construction out of `MergedStoreView._build()` into a dedicated composer.

Responsibilities:

- collect virtual nodes from all modules
- merge nodes with identical `SourceKey`
- merge labels by set union
- merge props deterministically
- collect virtual edges after node merge
- map `SourceKey -> virtual id`
- preserve old `path/ref/name` props for tool-facing compatibility

This is the phase that makes independent modules safe.

Merge rule:

- `id/project/labels/src` are reserved by core.
- `labels` are unioned.
- `props` are merged in module order.
- Later modules may fill missing fields, but overwriting should be explicit.
- Source-owned fields should be tracked for future source sync.

### Phase 4: Split Current Modules Into Flat Modules

Refactor current behavior into independent modules:

#### `fs_tree.py`

Owns only:

- directory nodes
- generic file nodes
- file/dir parent-child edges
- generic file source handle: `path`, `open`

Does not know about:

- CSV
- SQLite
- JSON/YAML/XML/TOML
- text classification

#### `csv_schema.py`

Owns:

- CSV/TSV file label contribution: `csv`
- `delimiter`
- `column_count`
- bounded `row_count`
- column virtual nodes
- file-to-column edges

Does not import `fs_tree.py`.

It emits the same file `SourceKey` as `fs_tree.py` for the file node, plus column keys such as:

```text
SourceKey(kind="csv_col", path="people.csv", entity="score")
```

#### `sqlite_schema.py`

Owns:

- SQLite file label contribution: `db`
- `table_count`, `view_count`, `index_count`
- table/view/column/FK virtual nodes
- schema edges
- SQLite-specific `src` port such as `db_connect`

It should not use shared `stores/utils/db.py`. Tiny helpers can be local, or the FS source adapter can expose `connect_sqlite`.

It should not compute expensive profile data by default. Avoid full `row_count` unless it is explicitly cost-gated.

#### `serialized_meta.py`

Owns:

- JSON/YAML/XML/TOML/HCL labels
- small-file `line_count`, `char_count`
- shallow top-level structure metadata

It should not do deep recursive JSON pattern extraction; that remains extractor work.

#### `text_meta.py`

Owns:

- text recognition
- text label contribution
- bounded encoding detection
- small-file `line_count`, `char_count`

It should not compute character distributions or semantic summaries.

### Phase 5: Replace Static Module Imports With Dynamic Registry

Move module discovery to a registry that does not force peer imports from `storage/stores/__init__.py`.

Example:

```python
DEFAULT_SOURCE_MODULES = {
    "fs": [
        "storage.stores.fs_tree:FileSystemTreeModule",
        "storage.stores.csv_schema:CSVSchemaModule",
        "storage.stores.sqlite_schema:SQLiteSchemaModule",
        "storage.stores.serialized_meta:SerializedMetaModule",
        "storage.stores.text_meta:TextMetaModule",
    ],
}
```

`stores.create_store(...)` uses `importlib` to load module classes.

The registry imports modules centrally. Modules still do not import each other.

Later, project config can override modules:

```yaml
projects:
  demo:
    source:
      type: fs
      path: ./demo
      modules:
        - fs_tree
        - csv_schema
        - sqlite_schema
```

### Phase 6: Enforce The Dependency Rule

Add a script test, likely inside `scripts/storage/test_store.py` or a separate `scripts/storage/check_store_modules.py`, that scans `storage/stores/*.py` imports.

Rule:

- Source modules may import `storage.stores.base`.
- Source modules may not import `storage.store`, `storage.workspace`, `storage.enricher`, `storage.src`, or any `storage.stores.<peer>`.
- `registry.py` is exempt.
- `base.py` is exempt.

This should run in CI or at least in the storage test script.

## Migration Notes For Existing Code

### `FSModule`

Current `FSModule` is doing too much:

- filesystem tree discovery
- file type classification
- generic virtual properties
- CSV light metadata
- serialized light metadata
- text light metadata
- SQLite source handle binding
- backend artifact filtering

It should be split. The future `fs_tree.py` should only discover file/dir nodes and generic source handles.

### `CSVSchemaModule`

This module is already close to the target because it only imports `base` from storage internals. It still reads `store.project_path` and `_backend_db_path`; those should move to `ctx.source`.

### `SQLiteSchemaModule`

This module must stop importing `stores.utils.db`. It should either use `sqlite3` directly or use `ctx.source.connect_sqlite`.

It also currently computes `row_count` for tables and views. That is arguably profiling, not schema projection. In the flat architecture, default SQLite schema projection should prefer cheap facts:

- table names
- view names
- columns
- declared types
- primary key
- physical foreign keys

`row_count` should be optional or left to extractor profiling.

### `text.py`

This file should not remain as a peer helper. Text recognition belongs in `text_meta.py`, or in a source adapter classification service if multiple modules need it.

### `enricher.py`

The current virtual-property enricher creates another cross-module abstraction. It should be removed after file/meta modules are explicit. Each module should own the virtual fields it contributes.

## Risks

### Duplicate Virtual Nodes

This is the largest risk. Splitting modules before adding `SourceKey` merge will produce duplicate file nodes.

Mitigation: implement `VirtualGraphComposer` first.

### Tool-Facing Ref Compatibility

Tools currently expect properties like `name`, `path`, and `ref`. Even if storage internally moves to `SourceKey`, virtual nodes must keep these props for display and agent-facing use.

Mitigation: keep compatibility props on virtual nodes until tools are migrated.

### Materialization Semantics

The current materialization closure uses string refs from `get_virtual_neighbors`. After structured keys, materialization must close over `SourceKey`, not string paths.

Mitigation: support both protocols temporarily:

- old: string refs
- new: `SourceKey`

Then remove old string-based closure once tests pass.

### Source Access Scope Creep

`SourceAdapter` should stay thin. It should provide native access, not high-level analytics.

Allowed:

- walk
- stat
- open
- native connection ports

Not allowed:

- top-k
- cardinality
- semantic summaries
- relationship inference

Those remain extractor/explorer work.

### Performance

`MergedStoreView` currently builds all module virtual nodes for each read query. More flat modules will make this worse if unchanged.

Mitigation:

- keep per-module fingerprint cache
- introduce query-aware discovery later
- keep expensive metadata cost-gated
- avoid full data scans in source modules

## Definition Of Done

The refactor is complete when:

- `storage/stores/*.py` source modules import only `storage.stores.base` from storage internals.
- No source module receives a `Store` instance.
- No source module reads Store private fields.
- `storage/stores/utils/` is removed.
- `storage/enricher.py` is removed or no longer used by modules.
- `storage/src.py` is replaced by a base-level `SourceHandle` contract or kept only as a compatibility re-export.
- `MergedStoreView` merges virtual nodes by structured `SourceKey`.
- The storage test script passes.
- A dependency check fails if a module imports another module under `storage/stores/`.

## Recommended Order

Do not start by splitting files. The safe order is:

1. Add `ModuleContext` and `SourceAdapter`.
2. Add `VirtualNode`, `VirtualEdge`, and `SourceKey`.
3. Add `VirtualGraphComposer` and merge virtual nodes by key.
4. Convert `CSVSchemaModule` to context first; it is the simplest module.
5. Convert `SQLiteSchemaModule` to context and remove `stores.utils.db`.
6. Split `FSModule` into `fs_tree`, `serialized_meta`, and `text_meta`.
7. Move source handle contract into `base`.
8. Remove `enricher.py`, `src.py` imports, and `stores/utils`.
9. Add dependency enforcement test.

This order keeps the graph behavior stable while removing coupling gradually.
