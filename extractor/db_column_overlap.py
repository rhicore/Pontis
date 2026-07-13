"""Storage-backed extractor for column value/name overlap entities.

This module is deliberately a thin facade: graph I/O and pipeline orchestration
remain here, while candidate filters, value matchers, and evidence grouping
live in focused modules under ``extractor.utils``.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Dict, Iterable, Optional

from extractor.utils.overlap_candidates import (
    _collect_pipeline_candidate_pairs,
    _collect_value_candidate_pairs,
    _normalized_key_name,
    _should_keep_value_candidate,
)
from extractor.utils.overlap_evidence import (
    _collapse_same_table_group_columns,
    _detect_name_overlaps,
    _group_pair_overlaps,
    _group_overlap_name,
    _merge_overlap_evidence,
)
from extractor.utils.overlap_options import TABLE_COLUMN_BATCH_SIZE, OverlapOptions, _resolve_options
from extractor.utils.overlap_filter_pipeline import count_pre_value_candidates, run_overlap_filter_pipeline
from extractor.utils.overlap_value_matchers import (
    _detect_column_overlaps,
    _detect_column_overlaps_metadata_sample,
    _split_domain_pair_overlaps,
)
from extractor.utils.refs import neo4j_props
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

def generate(
    workspace: Workspace,
    config=None,
    *,
    value_overlap_enabled: bool | None = None,
    name_overlap_enabled: bool | None = None,
    same_schema_only: bool | None = None,
    skip_same_table_group: bool | None = None,
    same_table_overlap_enabled: bool | None = None,
    same_table_group_representative_only: bool | None = None,
    domain_filter_enabled: bool | None = None,
    shape_filter_enabled: bool | None = None,
    key_like_only: bool | None = None,
    require_name_token_overlap: bool | None = None,
    name_token_overlap_first: bool | None = None,
    require_repeated_key_name: bool | None = None,
    top_k_per_column: int | None = None,
    generic_token_top_k: int | None = None,
    max_value_candidate_pairs: int | None = None,
    value_match_method: str | None = None,
    minhash_num_perm: int | None = None,
    minhash_min_matching_hashes: int | None = None,
    minhash_jaccard_threshold: float | None = None,
    minhash_max_sql_verify_pairs: int | None = None,
    snowflake_minhash_column_batch_size: int | None = None,
    snowflake_minhash_value_partitions: int | None = None,
    snowflake_minhash_max_warehouse_running: int | None = None,
    snowflake_minhash_warehouse_poll_seconds: int | None = None,
    lazo_containment_threshold: float | None = None,
    lazo_confidence: float | None = None,
    sample_bloom_sample_size: int | None = None,
    sample_bloom_false_positive_rate: float | None = None,
    sample_bloom_initial_capacity: int | None = None,
    sample_bloom_growth_factor: int | None = None,
    sample_bloom_min_hits: int | None = None,
    sample_bloom_sample_rows: int | None = None,
    column_domain_enabled: bool | None = None,
    pattern_table_domain_enabled: bool | None = None,
    pattern_table_domain_threshold: float | None = None,
    filter_pipeline=None,
) -> None:
    """为所有 storage-backed database projects 检测列值重叠."""
    logger.info("=== Generating column overlaps ===")
    options = _resolve_options(
        config,
        value_overlap_enabled=value_overlap_enabled,
        name_overlap_enabled=name_overlap_enabled,
        same_schema_only=same_schema_only,
        skip_same_table_group=skip_same_table_group,
        same_table_overlap_enabled=same_table_overlap_enabled,
        same_table_group_representative_only=same_table_group_representative_only,
        domain_filter_enabled=domain_filter_enabled,
        shape_filter_enabled=shape_filter_enabled,
        key_like_only=key_like_only,
        require_name_token_overlap=require_name_token_overlap,
        name_token_overlap_first=name_token_overlap_first,
        require_repeated_key_name=require_repeated_key_name,
        top_k_per_column=top_k_per_column,
        generic_token_top_k=generic_token_top_k,
        max_value_candidate_pairs=max_value_candidate_pairs,
        value_match_method=value_match_method,
        minhash_num_perm=minhash_num_perm,
        minhash_min_matching_hashes=minhash_min_matching_hashes,
        minhash_jaccard_threshold=minhash_jaccard_threshold,
        minhash_max_sql_verify_pairs=minhash_max_sql_verify_pairs,
        snowflake_minhash_column_batch_size=snowflake_minhash_column_batch_size,
        snowflake_minhash_value_partitions=snowflake_minhash_value_partitions,
        snowflake_minhash_max_warehouse_running=snowflake_minhash_max_warehouse_running,
        snowflake_minhash_warehouse_poll_seconds=snowflake_minhash_warehouse_poll_seconds,
        lazo_containment_threshold=lazo_containment_threshold,
        lazo_confidence=lazo_confidence,
        sample_bloom_sample_size=sample_bloom_sample_size,
        sample_bloom_false_positive_rate=sample_bloom_false_positive_rate,
        sample_bloom_initial_capacity=sample_bloom_initial_capacity,
        sample_bloom_growth_factor=sample_bloom_growth_factor,
        sample_bloom_min_hits=sample_bloom_min_hits,
        sample_bloom_sample_rows=sample_bloom_sample_rows,
        column_domain_enabled=column_domain_enabled,
        pattern_table_domain_enabled=pattern_table_domain_enabled,
        pattern_table_domain_threshold=pattern_table_domain_threshold,
        filter_pipeline=filter_pipeline,
    )
    logger.info("  Options: %s", options)

    db_rows = workspace.cypher(
        """
        MATCH (d:db)
        WITH d, coalesce(d._db_connect, d.db_connect) AS db_connect
        WHERE (d._ref IS NOT NULL OR d.name IS NOT NULL) AND db_connect IS NOT NULL
        RETURN d, db_connect
        ORDER BY coalesce(d._ref, d.name)
        """
    )
    if not db_rows:
        logger.info("  No db nodes found")
        return

    for db_row in db_rows:
        db_node = db_row.get("d") or {}
        db_connect = db_row.get("db_connect") or db_node.get("_db_connect") or db_node.get("db_connect")
        db_ref = str(db_node.get("_ref") or db_node.get("path") or db_node.get("name") or "")
        if not db_ref:
            continue
        try:
            _generate_for_database(db_ref, db_node, db_connect, workspace, options)
        except Exception as e:
            logger.warning(f"Failed to generate overlaps for {db_ref}: {e}")


def _generate_for_database(
    db_ref: str,
    db_node: dict,
    db_connect,
    workspace: Workspace,
    options: OverlapOptions,
) -> bool:
    """为单个数据库检测列值重叠."""
    if not callable(db_connect):
        logger.info("  Skipping %s: no storage db_connect handle", db_ref)
        return False
    dialect = str(getattr(db_connect, "dialect", "") or db_node.get("dialect") or "sqlite").lower()

    columns_info = _load_db_columns(workspace, db_ref)
    if not columns_info:
        return False

    if len(columns_info) < 2:
        logger.info(f"  Skipping {db_ref}: only {len(columns_info)} columns")
        return False

    _delete_existing_overlaps(db_ref, workspace)

    table_columns = defaultdict(list)
    for col in columns_info:
        table_columns[col['table']].append(col)
    table_group_memberships = _load_table_group_memberships(
        workspace,
        table_names=table_columns.keys(),
        table_refs=(col.get("table_ref") for col in columns_info),
    )

    candidate_pairs, candidate_stats = _collect_pipeline_candidate_pairs(
        table_columns,
        table_group_memberships,
        options=options,
    )
    pair_overlaps, filter_stats = run_overlap_filter_pipeline(
        candidate_pairs,
        options=options,
        table_group_memberships=table_group_memberships,
        db_connect=db_connect,
        dialect=dialect,
    )
    logger.info(
        "  Overlap filter pipeline for %s: seed=%s; stages=%s",
        db_ref,
        candidate_stats["candidate_pairs"],
        filter_stats,
    )

    domain_value_overlaps, physical_pair_overlaps = _split_domain_pair_overlaps(pair_overlaps)
    value_overlaps = domain_value_overlaps + [
        overlap
        for overlap in (
            _collapse_same_table_group_columns(overlap, table_group_memberships)
            for overlap in _group_pair_overlaps(physical_pair_overlaps)
        )
        if overlap is not None
    ]
    name_overlaps = (
        _detect_name_overlaps(columns_info, table_group_memberships)
        if options.name_overlap_enabled
        else []
    )
    overlap_groups = _merge_overlap_evidence(value_overlaps + name_overlaps)
    created_count = 0
    for overlap in overlap_groups:
        if _create_overlap_entity(db_ref, overlap, workspace):
            created_count += 1

    if created_count > 0:
        logger.info(f"  Overlaps: {db_ref} ({created_count} relations)")
    return True


def _load_db_columns(workspace: Workspace, db_ref: str) -> list[Dict]:
    table_rows = _load_db_tables(workspace, db_ref)
    columns: list[Dict] = []
    seen: set[str] = set()
    table_by_ref: dict[str, tuple[dict, list[str]]] = {}
    for table, schema_names in table_rows:
        table_ref = str(table.get("_ref") or table.get("path") or table.get("name") or "")
        if table_ref:
            table_by_ref[table_ref] = (table, schema_names)

    table_refs = sorted(table_by_ref)
    for batch_start in range(0, len(table_refs), TABLE_COLUMN_BATCH_SIZE):
        batch_refs = table_refs[batch_start:batch_start + TABLE_COLUMN_BATCH_SIZE]
        for table_ref, col in _load_table_columns_batch(workspace, table_refs=batch_refs):
            table, schema_names = table_by_ref.get(table_ref, ({}, []))
            table_name = str(table.get("table_name") or table.get("name") or "")
            if not table_name:
                continue
            col_ref = str(col.get("_ref") or col.get("path") or "")
            column_name = str(col.get("column_name") or col.get("name") or "")
            if not col_ref or not column_name:
                continue
            if col_ref in seen:
                continue
            seen.add(col_ref)
            schema_name = str(table.get("schema_name") or col.get("schema_name") or "")
            if not schema_name:
                schema_name = schema_names[0] if len(schema_names) == 1 else ""
            columns.append({
                "entity_name": col_ref,
                "db_ref": db_ref,
                "table": table_ref,
                "table_ref": table_ref,
                "table_name": table_name,
                "schema_name": schema_name,
                "column": column_name,
                "column_ref": col_ref,
                "data_type": str(col.get("data_type") or _column_type_from_labels(col)),
                "cardinality": int(col.get("cardinality") or 0),
                "min_length": _optional_int(col.get("min_length")),
                "max_length": _optional_int(col.get("max_length")),
                "avg_length": _optional_float(col.get("avg_length")),
                "min_value": _optional_float(col.get("min_value")),
                "max_value": _optional_float(col.get("max_value")),
                "null_percentage": _optional_float(col.get("null_percentage")),
                "sample": _decode_jsonish(col.get("sample"), default=[]),
                "topk": _decode_jsonish(col.get("topk"), default=[]),
            })
    return columns


def _load_db_tables(workspace: Workspace, db_ref: str) -> list[tuple[dict, list[str]]]:
    rows = workspace.cypher(
        """
        MATCH (d:db)
        WHERE d._ref = $db_ref OR d.name = $db_ref OR d.path = $db_ref
        MATCH (d)-[:RELATED_TO]-(t)
        WHERE (t:table OR t:view) AND (t._ref IS NOT NULL OR t.name IS NOT NULL)
        OPTIONAL MATCH (s:schema)--(t)
        WITH DISTINCT t, collect(DISTINCT s.name) AS schema_names
        RETURN t, schema_names
        UNION
        MATCH (d:db)
        WHERE d._ref = $db_ref OR d.name = $db_ref OR d.path = $db_ref
        MATCH (d)-[:RELATED_TO]-(s:schema)-[:RELATED_TO]-(t)
        WHERE (t:table OR t:view) AND (t._ref IS NOT NULL OR t.name IS NOT NULL)
        WITH DISTINCT t, collect(DISTINCT s.name) AS schema_names
        RETURN t, schema_names
        ORDER BY coalesce(t._ref, t.name)
        """,
        params={"db_ref": db_ref},
    )
    tables: dict[str, tuple[dict, set[str]]] = {}
    for row in rows:
        table = row.get("t") or {}
        table_ref = str(table.get("_ref") or table.get("path") or table.get("name") or "")
        if not table_ref:
            continue
        schema_names = {str(name) for name in row.get("schema_names") or [] if name}
        existing = tables.get(table_ref)
        if existing:
            existing[1].update(schema_names)
        else:
            tables[table_ref] = (table, schema_names)
    return [(table, sorted(schema_names)) for table, schema_names in tables.values()]


def _load_table_columns_batch(workspace: Workspace, *, table_refs: list[str]) -> list[tuple[str, dict]]:
    if not table_refs:
        return []
    rows = workspace.cypher(
        """
        MATCH (t)
        WHERE t._ref IN $table_refs OR t.path IN $table_refs OR t.name IN $table_refs
        MATCH (t)--(c:col)
        RETURN DISTINCT coalesce(t._ref, t.path, t.name) AS table_ref, c
        ORDER BY table_ref, c.ordinal_position, c.name
        """,
        params={"table_refs": table_refs},
    )
    return [(str(row.get("table_ref") or ""), row.get("c") or {}) for row in rows]


def _optional_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decode_jsonish(value, *, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return default
    text = value.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _delete_existing_overlaps(db_ref: str, workspace: Workspace) -> None:
    _write_cypher(
        workspace,
        """
        MATCH (d:db {project: $project})
        WHERE d._ref = $db_ref OR d.name = $db_ref OR d.path = $db_ref
        MATCH (d)-[:RELATED_TO*0..3]-(o:overlap)
        DETACH DELETE o
        """,
        params={"db_ref": db_ref},
    )


def _load_table_group_memberships(
    workspace: Workspace,
    *,
    table_names: Iterable[str],
    table_refs: Iterable[str],
) -> dict[str, set[str]]:
    """Return table -> table_group refs for graphs that have table_group nodes.

    BIRD projects usually have no table_group nodes, so this returns an empty
    mapping and the legacy overlap behavior is unchanged. For Spider-style
    graphs, same table_group members are physical partitions of one logical
    table and should not produce overlap candidates against each other.
    """

    lookup_values = sorted({
        str(value)
        for value in list(table_names) + list(table_refs)
        if value
    })
    if not lookup_values:
        return {}
    rows = workspace.cypher(
        """
        MATCH (g:table_group)--(t:table)
        WHERE t.name IN $values OR t._ref IN $values OR t.path IN $values
        RETURN t.name AS name,
               t._ref AS ref,
               t.path AS path,
               collect(DISTINCT coalesce(g._ref, g.name)) AS groups
        """,
        params={"values": lookup_values},
    )
    memberships: dict[str, set[str]] = {}
    for row in rows:
        groups = {str(group) for group in row.get("groups") or [] if group}
        if not groups:
            continue
        for key in (row.get("name"), row.get("ref"), row.get("path")):
            if key:
                memberships.setdefault(str(key), set()).update(groups)
    return memberships


def _column_type_from_labels(col_meta: Dict) -> str:
    labels = col_meta.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    for label in labels:
        label_text = str(label or "").strip()
        if not label_text or label_text.lower() == "col":
            continue
        return label_text
    return ""

def _create_overlap_entity(db_ref: str, overlap: Dict, workspace: Workspace) -> bool:
    """在 _entity/ 下为重叠关系创建实体（labels=["overlap"]）"""
    try:
        if "columns" in overlap:
            return _create_group_overlap_entity(db_ref, overlap, workspace)

        from_table = overlap['from_table']
        from_table_name = overlap.get('from_table_name') or from_table
        from_column = overlap['from_column']
        to_table = overlap['to_table']
        to_table_name = overlap.get('to_table_name') or to_table
        to_column = overlap['to_column']
        from_col_ref = overlap['from_ref']
        to_col_ref = overlap['to_ref']

        raw_from_table = from_table_name.split("--")[-1] if "--" in from_table_name else from_table_name
        raw_to_table = to_table_name.split("--")[-1] if "--" in to_table_name else to_table_name
        raw_from_col = from_column.split("--")[-1] if "--" in from_column else from_column
        raw_to_col = to_column.split("--")[-1] if "--" in to_column else to_column
        safe_from_col = raw_from_col.replace("/", "_").replace("\\", "_")
        safe_to_col = raw_to_col.replace("/", "_").replace("\\", "_")

        overlapname = f"{raw_from_table}.{safe_from_col}->{raw_to_table}.{safe_to_col}"
        reversename = f"{raw_to_table}.{safe_to_col}->{raw_from_table}.{safe_from_col}"

        existing_name = None
        if workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": overlapname}):
            existing_name = overlapname
        elif workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": reversename}):
            existing_name = reversename

        if existing_name:
            _connect_overlap_edges(workspace, existing_name, from_table, to_table, from_col_ref, to_col_ref)
            return False

        _write_cypher(
            workspace,
            "CREATE (o:overlap {name: $name}) SET o += $props SET o.project = $project",
            params={
                "name": overlapname,
                "props": neo4j_props({
                    "labels": ["overlap"],
                    "table_scope": _table_scope([from_table, to_table]),
                    "sources": overlap.get("sources", ["value_domain"]),
                    "stats": overlap["stats"],
                    "filter_evidence": overlap.get("filter_evidence", {}),
                    "filter_pipeline": overlap.get("filter_pipeline", []),
                    "created_at": __import__("datetime").datetime.now().isoformat(),
                }),
            },
        )

        _connect_overlap_edges(workspace, overlapname, from_table, to_table, from_col_ref, to_col_ref)

        return True

    except Exception as e:
        logger.debug(f"Could not create overlap file: {e}")
        return False


def _create_group_overlap_entity(db_ref: str, overlap: Dict, workspace: Workspace) -> bool:
    """Create one overlap entity for a fully-connected value-domain column group."""
    try:
        columns = overlap["columns"]
        overlapname = _group_overlap_name(columns)

        existing = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": overlapname})
        if existing:
            _connect_group_overlap_edges(workspace, overlapname, columns)
            return False

        _write_cypher(
            workspace,
            "CREATE (o:overlap {name: $name}) SET o += $props SET o.project = $project",
            params={
                "name": overlapname,
                "props": neo4j_props({
                    "labels": ["overlap"],
                    "table_scope": _table_scope([column["table"] for column in columns]),
                    "sources": overlap.get("sources", ["value_domain"]),
                    "pair_stats": overlap.get("pair_stats", []),
                    "domain_sides": overlap.get("domain_sides", []),
                    "stats": overlap["stats"],
                    "created_at": __import__("datetime").datetime.now().isoformat(),
                }),
            },
        )

        _connect_group_overlap_edges(workspace, overlapname, columns)
        return True

    except Exception as e:
        logger.debug(f"Could not create group overlap entity: {e}")
        return False


def _table_scope(tables: list[str]) -> str:
    return "intra_table" if len(set(tables)) == 1 else "inter_table"


def _connect_group_overlap_edges(workspace: Workspace, overlap_name: str, columns: list[Dict]) -> None:
    table_refs = {column["table"] for column in columns}
    column_refs = {column["ref"] for column in columns}
    for entity_ref in sorted(table_refs | column_refs):
        _connect_overlap_edge(workspace, overlap_name, entity_ref)


def _connect_overlap_edges(
    workspace: Workspace,
    overlap_name: str,
    from_table: str,
    to_table: str,
    from_col_ref: str,
    to_col_ref: str,
) -> None:
    """Connect overlap to both tables and both column endpoints."""
    for entity_ref in (from_table, to_table, from_col_ref, to_col_ref):
        _connect_overlap_edge(workspace, overlap_name, entity_ref)


def _connect_overlap_edge(workspace: Workspace, overlap_name: str, entity_ref: str) -> None:
    _write_cypher(
        workspace,
        """
        MATCH (o {name: $overlap_name, project: $project})
        MATCH (a {project: $project})
        WHERE a.name = $entity_ref
           OR a._ref = $entity_ref
           OR a.ref = $entity_ref
           OR a.path = $entity_ref
        MERGE (a)-[:RELATED_TO]->(o)
        """,
        params={"entity_ref": entity_ref, "overlap_name": overlap_name},
    )


def _write_cypher(workspace: Workspace, query: str, params: dict | None = None) -> list:
    """Execute extractor-internal writes without user-query project rewriting."""

    params = dict(params or {})
    rows: list = []
    for project in workspace.active_projects:
        store = workspace._get_store(project)
        if store is None:
            continue
        scoped_params = dict(params)
        scoped_params["project"] = project
        with store.execution_lock:
            rows.extend(store.execute_cypher(query, params=scoped_params))
    return rows
