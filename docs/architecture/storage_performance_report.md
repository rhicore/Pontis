# Storage Performance Report

Date: 2026-05-13

This report records the current performance shape of Pontis storage after the latest tool-layer cleanup. It focuses on observed bottlenecks, likely scaling risks, and a practical improvement path.

## Summary

The worst latency seen by the agent was mostly tool-layer overhead, not raw SQLite or source database speed. The immediate fixes reduced repeated `meta` and `find` calls from seconds to milliseconds in the same `Workspace` lifetime.

The remaining storage bottleneck is cold read setup: `Workspace.cypher(...)` builds a merged graph view from persisted graph data plus source-module virtual graph data. That cost is now cached, so warm reads are fast, but cold reads and invalidation after writes still pay the full merge cost.

## Current Measurements

Measured on BIRD dev databases with the current repository state:

| Project | Merged build | `MATCH (n) RETURN n` cold | Warm | Rows |
| --- | ---: | ---: | ---: | ---: |
| `codebase_community` | ~0.26s | ~0.22s | ~0.001s | 188 |
| `california_schools` | ~0.19s | ~0.19s | ~0.001s | 126 |
| `card_games` | ~0.52s | ~0.28s | ~0.001s | 175 |

Module build cost is not the main issue:

| Project | `fs` nodes | `csv_schema` nodes | `db_schema` nodes |
| --- | ---: | ---: | ---: |
| `codebase_community` | 12 in ~0.006s | 40 in ~0.004s | 92 in ~0.019s |
| `california_schools` | 7 in ~0.004s | 15 in ~0.002s | 94 in ~0.002s |
| `card_games` | 12 in ~0.005s | 30 in ~0.003s | 125 in ~0.035s |

The expensive part of merged build is identity matching. `MergedStoreView._build()` calls each module's `match_query(...)` for virtual nodes, and each returned query is executed separately against the base store. On `codebase_community`, one profile showed 144 internal Cypher calls during one merged-view build.

## Tool-Layer Fixes Already Applied

These were fixed before writing this report:

- `Workspace.cypher(...)` caches read-only `MergedStoreView` instances by project, store version, and module identity.
- `display_ref_for_node(...)` now uses local `path` / `ref` metadata before falling back to graph queries.
- `resolve_entity(...)` resolves wildcard refs through structured ref matching instead of reparsing formatted text output.
- `meta(property=[...])` returns ordinary properties without querying all neighbors.
- semantic entity lookup delays display-ref formatting until after ranking and pagination.
- `query` fetches only `limit + 1` rows instead of `fetchall()`.
- `grep` streams search output for content mode and stops after enough lines for the requested page.

Observed tool timings on `codebase_community` after fixes:

| Operation | Before | After |
| --- | ---: | ---: |
| `meta(codebase_community.sqlite/posts)` | ~6.5s | ~0.37s cold, ~0.001s warm |
| `find(ref="codebase_community::*")` | ~26.5s | ~0.016s warm |
| `find(ref="codebase_community::*", query=...)` | ~26.4s | ~0.016-0.03s warm |
| `meta(..., property=["detail"])` | queried neighbors | ~0.001s warm |

## Storage Bottlenecks

### 1. Merged View Build Repeats Identity Queries

Current path:

```text
Workspace.cypher
  -> MergedStoreView(store, modules)
    -> for each module virtual node:
       -> mod.match_query(vnode)
       -> base_store._cypher_internal(query)
```

This behaves like a nested loop: virtual nodes multiplied by identity lookups. It is acceptable at hundreds of nodes, but it will not scale cleanly to thousands of files, tables, columns, or extracted entities.

Recommended fix:

- Keep `match_query(...)` as the public module contract.
- Add an internal batch identity matching layer for the common cases:
  - `ref = ...`
  - `path = ...`
  - `name = ...` with label guard
- Build `Store` lookup maps once per merged build:
  - `ref -> ent_id`
  - `path -> ent_id`
  - `(label, name) -> [ent_id]`
- Let `MergedStoreView` resolve common `MatchQuery` shapes without invoking the Cypher executor per virtual node.
- Fall back to Cypher only for uncommon match queries.

### 2. Cypher Executor Scans All Nodes For Simple Lookups

Queries such as:

```cypher
MATCH (n {ref: $ref}) RETURN n
MATCH (n:table {name: $name}) RETURN n
```

currently scan `store._id_index` and filter in Python. This is simple and correct, but it ignores the fact that most tool queries are exact lookup queries.

Recommended fix:

- Add in-memory secondary indexes inside `Store._build_index()`:
  - `by_name`
  - `by_ref`
  - `by_path`
  - `by_label`
  - optionally `(label, property, value)` for hot properties
- Teach `CypherExecutor._seed_nodes()` and `_execute_single()` to use these indexes when the query is a simple exact match.
- Preserve scan fallback for predicates such as `CONTAINS`, `STARTS WITH`, range comparisons, and multi-hop patterns.

### 3. Query Parsing And Planning Are Repeated

Pontis reparses every Cypher string. This is small compared with merged build today, but it becomes visible in agent loops because tools reuse the same query shapes many times.

Recommended fix:

- Add a small LRU cache around `parse_cypher(query, params=...)` keyed by query string only.
- Keep params outside the cache, as Neo4j recommends parameterized queries to improve plan reuse.
- Later, separate parse cache from execution-plan cache if the executor grows real planning.

### 4. Read Projection Materializes Full Nodes

Neo4j's query tuning guidance is to retrieve only necessary data and return only needed fields. Pontis currently often builds full node dictionaries even when tools only need names, labels, `ref`, or `src`.

Recommended fix:

- Extend `CypherExecutor` with a lightweight projection path for `RETURN n.name`, `RETURN n.ref`, and `RETURN n.src`.
- Avoid `_node_result(...)` full metadata expansion when return items only need simple fields.
- Add tool-specific queries that request only required fields.

### 5. Edge Access Is Good Enough But Not Indexed By Edge Type

Pontis already keeps `_adjacent[eid]`, so basic neighbor traversal is cheap. The next risk appears when relation-like nodes become high degree or when tools frequently ask for only one neighbor class such as `col`, `fk`, `overlap`, or `disambig`.

Recommended fix:

- Add an optional adjacency-by-label cache:
  - `(eid, neighbor_label) -> [neighbor_eid]`
- Use it in `meta(neighbor_label=...)` and common table/column/fk traversals.
- This is the local equivalent of vertex-centric indexing in graph databases.

## External References

The recommendations above line up with common graph database performance practice:

- Neo4j's Cypher tuning docs emphasize filtering as early as possible, retrieving only necessary data, limiting variable-length patterns, using parameters, and using labels/types explicitly for better plans: https://neo4j.com/docs/cypher-manual/4.0/query-tuning/
- Neo4j search-performance indexes are maintained copies of selected data used for efficient retrieval, with a write/storage tradeoff: https://neo4j.com/docs/cypher-manual/current/indexes/search-performance-indexes/
- Neo4j query-plan cache guidance supports parameterized repeated query shapes: https://neo4j.com/developer/kb/understanding-the-query-plan-cache/
- ArangoDB documents edge indexes and vertex-centric indexes for fast neighbor lookup and filtered traversal around high-degree vertices: https://docs.arangodb.com/3.10/index-and-search/indexing/basics/

## Recommended Implementation Order

1. Keep the current `MergedStoreView` cache. It removes the largest repeated agent-loop cost.
2. Add batch identity matching in `MergedStoreView._build()`.
3. Add `Store` secondary indexes for `ref`, `path`, `name`, and labels.
4. Use those indexes in `_execute_single()` and `_seed_nodes()`.
5. Add a parse cache for Cypher strings.
6. Add lightweight projection for scalar `RETURN` clauses.
7. Add adjacency-by-label only when relation-heavy projects show neighbor filtering as a real hotspot.

## Acceptance Targets

Near-term targets for BIRD-sized projects:

- Cold `MATCH (n) RETURN n`: under 100ms for projects under 500 graph-visible nodes.
- Warm `find(ref="project::*")`: under 20ms.
- Warm `meta(table)` full view: under 5ms.
- `meta(col, property=["detail"])`: under 2ms.
- Merged view rebuild after a write: under 100ms for projects under 500 graph-visible nodes.

Larger project targets should be set after adding synthetic benchmarks with 10k, 100k, and 1M virtual nodes.

## Benchmark Gaps

Current tests cover correctness but not storage performance regression. Add a small benchmark script under `scripts/storage/` that reports:

- persistent node count / edge count
- virtual node count / edge count by module
- merged build time
- cold and warm read times for common query shapes
- write materialization time
- repeated tool loop time for `meta` and `find`

The benchmark should print plain numbers and fail only on extreme regressions, so it can be used locally without making CI flaky.
