# Pontis KG Modeling Memory

## Do not duplicate graph topology as node metadata

When extracting official dataset metadata into the Pontis knowledge graph, keep
ownership and containment in graph edges, not repeated node properties.

Core rule:

- If an entity is already connected to its parent entity, do not repeat the
  parent identity in the child node's metadata.
- Node metadata should describe the node itself. Graph relationships should
  describe where the node belongs.
- Derived display paths, search refs, and fully qualified names should be
  computed from graph topology when needed, instead of being stored redundantly
  on every node.

Examples:

- A `column` node connected to a `table` node should not store `table_name`,
  `schema_name`, or `database_name` as normal metadata.
- A `table` node connected to a `schema` node should not store `schema_name` or
  `database_name` as normal metadata.
- A `schema` node connected to a `database` node should not store
  `database_name` as normal metadata.
- A `table_group` connected to its member `table` nodes should not duplicate the
  full metadata of each member table. It should store only group-level metadata
  such as `family`, `member_count`, `representative_members`, `common_columns`,
  `variable_columns`, `consistency`, and `agent_usage_hint`.

Rationale:

- Repeating parent names across child nodes creates synchronization problems.
- If one copied field conflicts with graph topology, the agent cannot tell which
  fact is authoritative.
- Pontis is a property graph; topology is first-class information. Use edges for
  containment and node properties for local facts.

Allowed exceptions:

- Internal identity fields such as `_ref`, `_db_ref`, or `_schema_ref` may exist
  if required by storage/indexing code, but they are implementation details and
  should not be treated as agent-facing metadata.
- A fully qualified name may be stored only when it is an official source field
  that has semantic value by itself. It must not become the source of truth for
  containment if graph edges already encode the same relationship.
- Denormalized counters such as `column_count` or `member_count` are allowed
  because they summarize adjacent entities rather than duplicate their identity.

## Spider2-Snow official metadata mapping policy

Spider2-Snow official resources should be translated into existing Pontis
ontology entities without creating redundant parent fields.

Preferred graph topology:

```text
(database)-[:RELATED_TO]->(schema)
(schema)-[:RELATED_TO]->(table)
(table)-[:RELATED_TO]->(column)
```

Entity-local metadata only:

| Entity | Metadata from official DDL.csv / JSON |
|---|---|
| `db` | `name`, `table_count`, `view_count` |
| `schema` | `name`, `table_count`, `view_count` |
| `table` | `name`, `official_table_description`, `ddl`, `column_count`, `primary_key` |
| `view` | `name`, `official_view_description`, `ddl`, `column_count` |
| `col` | `name`, `ordinal_position`, `data_type`, `official_column_description`, `sample`, `not_null`, `default_value` |
| `fk` | `name`, `source_columns`, `target_table`, `target_columns`, `constraint_name`, `brief` |
| `table_group` | `family`, `member_count`, `representative_members`, `common_columns`, `variable_columns`, `consistency`, `cognitive_shape`, `agent_usage_hint`, `schema_reading_strategy` |

Official file field mapping:

| Official field | Extracted local metadata / entity |
|---|---|
| directory `<db_id>/` | `db.name` |
| directory `<schema_name>/` | `schema.name` |
| `DDL.csv.table_name` | `table.name` or `view.name` |
| `DDL.csv.description` | `table.official_table_description` or `view.official_view_description` |
| `DDL.csv.DDL` | `table.ddl`, `view.ddl`, `col.name`, `col.ordinal_position`, `col.data_type`, `col.not_null`, `col.default_value`, `table.primary_key`, explicit `fk` nodes when the DDL declares foreign keys |
| `JSON.table_name` | table identity for matching the `table` node, not repeated child metadata |
| `JSON.table_fullname` | official matching key; use it to connect `database -> schema -> table`, not to duplicate parent fields on child nodes |
| `JSON.column_names` | `col.name`, `col.ordinal_position` |
| `JSON.column_types` | `col.data_type` |
| `JSON.description` | `col.official_column_description` |
| `JSON.sample_rows` | `col.sample` |

Current deterministic Spider2-Snow extractor scope:

- Read only official files under `resource/databases`.
- Do not read question-level external Markdown documents.
- Do not create new topic/document/snapshot entities in the deterministic
  extractor.
- Create `table_group` only as a derived cognitive unit connected to existing
  `table` nodes.

## Spider2-Snow preprocessing workflow

Spider2-Snow should not reuse the BIRD requirement that every physical table
and every column must receive AI-authored `brief/detail`. The official schemas
are often too large for that target.

Recommended stages. In scripts, Spider2-Snow should mirror BIRD's shape: use
one dataset-specific preprocessing entry point that calls both extractor and
explorer modules in order, instead of maintaining a separate explorer script.

1. Extract official structure with `spider2_snow_schema`.
   This creates the authoritative `db -> schema -> table/view -> col` graph from
   official DDL CSV and JSON files.
2. Extract deterministic physical groups with `db_table_group`.
   This creates an independent `table_group` layer for date/version/region/
   chromosome/shard families. It must not replace or delete official
   `schema -> table` edges.
3. Explore semantic topics with `agent_topic_group`.
   This is an agent/explorer step, not a deterministic extractor. It creates
   `topic` nodes as a semantic navigation layer over schemas, table groups, and
   standalone tables.
4. Prepare compressed navigation details with `agent_spider_navigation_prepare`.
   Required `brief/detail` targets are only `schema`, `topic`, `table_group`,
   and standalone `table`. Tables already covered by a `table_group` and all
   columns are evidence, not completion targets.
5. Write `schema_landscape` after topic/table-group exploration.
   This deterministic node summarizes the database-level navigation entrypoint.
6. Run `semantic_embedding` after detail-bearing navigation nodes exist.

Question-level external Markdown should be supplied at solving time for the
specific instance that references it. It should not be pre-extracted into the
global database graph by the deterministic Spider2-Snow extractor.

## Generic extractor boundary

Generic database extractors should discover database structure from the
knowledge graph and access the underlying database only through storage-owned
handles such as `_db_connect`.

For example, `db_column_overlap` is a generic storage-backed extractor:

- read `db/table/view/col` candidates from the graph;
- resolve the database connection through `_db_connect`;
- query column values through the returned DB-API connection;
- write derived `overlap` nodes back into the graph;
- filter same-`table_group` member tables because they are physical partitions
  of one logical object, not join candidates.

Dataset-specific extractors are allowed only when they import official
dataset files that are not exposed as generic source entities. Their file names
must carry the dataset prefix, such as `bird_official_description_extract` and
`spider2_snow_schema`.

## Storage handle access rule

All extractor and explorer modules should access source content through handles
exposed by KG entities. Access capabilities are determined by graph labels and
their storage materialization:

- `file` entities expose `_file_open` / `file_open`.
- `db` entities expose `_db_connect` / `db_connect`.
- Derived entities such as `table`, `view`, `col`, and `fk` may inherit or carry
  the relevant database handle when storage materializes them.

Modules should first locate entities in the KG, then use the handle attached to
the entity or its parent entity. They should not rediscover local paths, scan
dataset folders, or open SQLite/filesystem paths directly unless the module is
an explicitly dataset-specific importer with a dataset prefix.

Current handle-backed examples:

- `db_column_stats_approx`: discovers `db/table/view/col` from KG and profiles
  columns through `_db_connect`.
- `db_fk_validate`: discovers `fk` from KG and validates joins through
  `_db_connect`.
- `csv_column_stats` / `json_pattern`: resolve `file` entities and read content
  through `_file_open`.
