"""Shared storage-backed database and column catalog for extractors.

This module owns graph-to-database discovery and converts physical graph
columns into one stable metadata shape.  Candidate algorithms should consume
this catalog instead of importing private helpers from another extractor.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from extractor.utils.overlap_options import TABLE_COLUMN_BATCH_SIZE
from storage.workspace import Workspace


@dataclass(frozen=True)
class DatabaseContext:
    ref: str
    node: dict
    connect: Any
    dialect: str


def iter_database_contexts(workspace: Workspace) -> list[DatabaseContext]:
    """Return database sources that expose an executable storage handle."""

    rows = workspace.cypher(
        """
        MATCH (d:db)
        WITH d, coalesce(d._db_connect, d.db_connect) AS db_connect
        WHERE (d._ref IS NOT NULL OR d.name IS NOT NULL) AND db_connect IS NOT NULL
        RETURN d, db_connect
        ORDER BY coalesce(d._ref, d.name)
        """
    )
    contexts: list[DatabaseContext] = []
    for row in rows:
        node = row.get("d") or {}
        ref = str(node.get("_ref") or node.get("path") or node.get("name") or "")
        connect = row.get("db_connect") or node.get("_db_connect") or node.get("db_connect")
        if not ref or not callable(connect):
            continue
        contexts.append(DatabaseContext(
            ref=ref,
            node=node,
            connect=connect,
            dialect=str(getattr(connect, "dialect", "") or node.get("dialect") or "sqlite").lower(),
        ))
    return contexts


def load_database_columns(
    workspace: Workspace,
    db_ref: str,
    *,
    exclude_logical_members: bool = False,
) -> list[dict]:
    """Load physical columns and their reusable static evidence."""

    table_rows = _load_database_tables(workspace, db_ref)
    columns: list[dict] = []
    seen: set[str] = set()
    table_by_ref: dict[str, tuple[dict, list[str]]] = {}
    for table, schema_names in table_rows:
        table_ref = str(table.get("_ref") or table.get("path") or table.get("name") or "")
        if table_ref:
            table_by_ref[table_ref] = (table, schema_names)

    table_refs = sorted(table_by_ref)
    for batch_start in range(0, len(table_refs), TABLE_COLUMN_BATCH_SIZE):
        batch_refs = table_refs[batch_start:batch_start + TABLE_COLUMN_BATCH_SIZE]
        for table_ref, col in _load_table_columns_batch(
            workspace,
            table_refs=batch_refs,
            exclude_logical_members=exclude_logical_members,
        ):
            table, schema_names = table_by_ref.get(table_ref, ({}, []))
            table_name = str(table.get("table_name") or table.get("name") or "")
            col_ref = str(col.get("_ref") or col.get("path") or "")
            column_name = str(col.get("column_name") or col.get("name") or "")
            if not table_name or not col_ref or not column_name or col_ref in seen:
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
                "data_type": str(col.get("data_type") or column_type_from_labels(col)),
                "cardinality": int(col.get("cardinality") or 0),
                "min_length": _optional_int(col.get("min_length")),
                "max_length": _optional_int(col.get("max_length")),
                "avg_length": _optional_float(col.get("avg_length")),
                "min_value": _optional_float(col.get("min_value")),
                "max_value": _optional_float(col.get("max_value")),
                "null_percentage": _optional_float(col.get("null_percentage")),
                "sample": decode_jsonish(col.get("sample"), default=[]),
                "topk": decode_jsonish(col.get("topk"), default=[]),
                "domain_profile": decode_jsonish(col.get("domain_profile"), default={}),
            })
    return columns


def load_table_group_memberships(
    workspace: Workspace,
    *,
    table_names: Iterable[str],
    table_refs: Iterable[str],
) -> dict[str, set[str]]:
    """Return physical-table to table-group membership."""

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


def decode_jsonish(value, *, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value.strip())
    except json.JSONDecodeError:
        return default


def column_type_from_labels(col_meta: dict) -> str:
    labels = col_meta.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    for label in labels:
        text = str(label or "").strip()
        if text and text.lower() != "col":
            return text
    return ""


def _load_database_tables(workspace: Workspace, db_ref: str) -> list[tuple[dict, list[str]]]:
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


def _load_table_columns_batch(
    workspace: Workspace,
    *,
    table_refs: list[str],
    exclude_logical_members: bool = False,
) -> list[tuple[str, dict]]:
    if not table_refs:
        return []
    column_pattern = "(c:col:standalone)" if exclude_logical_members else "(c:col)"
    rows = workspace.cypher(
        f"""
        MATCH (t)
        WHERE t._ref IN $table_refs OR t.path IN $table_refs OR t.name IN $table_refs
        MATCH (t)--{column_pattern}
        RETURN DISTINCT coalesce(t._ref, t.path, t.name) AS table_ref, c
        ORDER BY table_ref, c.ordinal_position, c.name
        """,
        params={"table_refs": table_refs},
    )
    return [(str(row.get("table_ref") or ""), row.get("c") or {}) for row in rows]


def _optional_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
