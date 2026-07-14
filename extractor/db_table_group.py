"""Database table-group extractor.

This deterministic pass creates ``:table_group`` nodes for physical table
families such as date shards, year partitions, quarterly tables, release
versions, chromosome shards, and numeric suffix shards. It is intentionally
conservative: by default it only writes groups with at least three member
tables, so ordinary business tables are not forced into artificial groups.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from extractor.utils.refs import neo4j_props
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(17|18|19|20)\d{2}")
_YYYYMM_RE = re.compile(r"(17|18|19|20)\d{4}")
_YYYYMMDD_RE = re.compile(r"(17|18|19|20)\d{6}")
_COMPACT_YY_RE = re.compile(r"^([A-Z][A-Z0-9]*?[A-Z])(\d{2})$")
_COMPACT_PREFIX_DIGIT_YY_RE = re.compile(r"^([A-Z][A-Z0-9]*\d)(\d{2})$")
_GEO_SUFFIXES = {
    "ALABAMA",
    "ALASKA",
    "AMERICAN_SAMOA",
    "ARIZONA",
    "ARKANSAS",
    "CALIFORNIA",
    "COLORADO",
    "CONNECTICUT",
    "DELAWARE",
    "DISTRICT_OF_COLUMBIA",
    "FLORIDA",
    "GEORGIA",
    "GUAM",
    "HAWAII",
    "IDAHO",
    "ILLINOIS",
    "INDIANA",
    "IOWA",
    "KANSAS",
    "KENTUCKY",
    "LOUISIANA",
    "MAINE",
    "MARYLAND",
    "MASSACHUSETTS",
    "MICHIGAN",
    "MINNESOTA",
    "MISSISSIPPI",
    "MISSOURI",
    "MONTANA",
    "NEBRASKA",
    "NEVADA",
    "NEW_HAMPSHIRE",
    "NEW_JERSEY",
    "NEW_MEXICO",
    "NEW_YORK",
    "NORTH_CAROLINA",
    "NORTH_DAKOTA",
    "NORTHERN_MARIANA_ISLANDS",
    "OHIO",
    "OKLAHOMA",
    "OREGON",
    "PENNSYLVANIA",
    "PUERTO_RICO",
    "RHODE_ISLAND",
    "SOUTH_CAROLINA",
    "SOUTH_DAKOTA",
    "TENNESSEE",
    "TEXAS",
    "UTAH",
    "VERMONT",
    "VIRGIN_ISLANDS",
    "VIRGINIA",
    "WASHINGTON",
    "WEST_VIRGINIA",
    "WISCONSIN",
    "WYOMING",
}
_GEO_SUFFIX_PATTERN = re.compile(rf"_(?:{'|'.join(sorted(_GEO_SUFFIXES, key=len, reverse=True))})$")
MAX_STORED_COLUMNS = 120
MAX_REPRESENTATIVE_MEMBERS = 3


@dataclass(frozen=True)
class TableInfo:
    db_ref: str
    table_ref: str
    schema_ref: str
    database_name: str
    schema_name: str
    table_name: str
    family: str
    pattern_types: tuple[str, ...]
    columns: tuple[str, ...]


def generate(
    workspace: Workspace,
    *,
    min_members: int = 3,
    include_singletons: bool = False,
) -> None:
    """Create table-group nodes from already materialized table/column nodes."""

    logger.info("=== DB table group extraction ===")

    tables = _load_tables(workspace)
    if not tables:
        logger.info("  No table nodes found")
        return

    grouped: dict[tuple[str, str, str, str], list[TableInfo]] = defaultdict(list)
    for table in tables:
        if not include_singletons and table.family == table.table_name.upper():
            continue
        grouped[(table.db_ref, table.schema_ref, table.schema_name, table.family)].append(table)

    summaries = []
    for key, members in grouped.items():
        if len(members) < max(1, min_members):
            continue
        summaries.append(_group_summary(key, members))

    summaries.sort(key=lambda item: (-item["member_count"], item["db_ref"], item["schema_name"], item["family"]))
    _delete_stale_groups(workspace, grouping_method="pattern_table_family_v1")

    created_or_updated = 0
    for summary in summaries:
        _upsert_group(workspace, summary)
        created_or_updated += 1

    logger.info(
        "  Table groups written: %s groups, %s member tables",
        created_or_updated,
        sum(item["member_count"] for item in summaries),
    )


def table_family_name(table_name: str) -> str:
    """Return a conservative family key for partition/version table names."""

    return _table_family(table_name)[0]


def _table_family(table_name: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(family_key, pattern_types)`` for a physical table name."""

    name = str(table_name or "").upper()
    patterns: list[str] = []

    def sub(pattern: re.Pattern | str, repl: str, pattern_type: str, value: str) -> str:
        new_value, count = re.subn(pattern, repl, value)
        if count:
            patterns.append(pattern_type)
        return new_value

    name = sub(_YYYYMMDD_RE, "YYYYMMDD", "date_shard_yyyymmdd", name)
    name = sub(_YYYYMM_RE, "YYYYMM", "month_shard_yyyymm", name)
    name = sub(_YEAR_RE, "YYYY", "year_shard_yyyy", name)
    name = sub(r"^REL\d+(?=_|$)", "REL#", "release_version", name)
    name = sub(r"_R\d+(?=_|$)", "_R#", "release_version", name)
    name = sub(r"_Q[1-4]\b", "_Q#", "quarter_shard", name)
    name = sub(r"__CHR(?:\d+|X|Y|MT|M)(?=_|$)", "__CHR#", "chromosome_shard", name)
    name = sub(r"_CHR(?:\d+|X|Y|MT|M)(?=_|$)", "_CHR#", "chromosome_shard", name)
    name = sub(r"_\d{1,3}\b", "_#", "numeric_suffix_shard", name)
    name = sub(_GEO_SUFFIX_PATTERN, "_GEO_REGION", "geo_region_shard", name)
    compact = _compact_year_family(name)
    if compact != name:
        patterns.append("compact_year_suffix")
        name = compact
    return name, tuple(dict.fromkeys(patterns))


def _compact_year_family(name: str) -> str:
    """Normalize compact FEC-style cycle suffixes such as INDIV20 or PAS220."""

    if "_" in name or len(name) < 4:
        return name
    match = _COMPACT_PREFIX_DIGIT_YY_RE.fullmatch(name)
    if match:
        return f"{match.group(1)}YY"
    match = _COMPACT_YY_RE.fullmatch(name)
    if match:
        return f"{match.group(1)}YY"
    return name


def _load_tables(workspace: Workspace) -> list[TableInfo]:
    table_rows = workspace.cypher(
        """
        MATCH (d:db)-[:RELATED_TO*1..2]-(t:table)
        WHERE t._ref IS NOT NULL
        RETURN DISTINCT d, t
        ORDER BY d._ref, t.schema_name, t.table_name
        """
    )
    columns_by_table: dict[str, tuple[str, ...]] = {}
    table_refs = [
        str((row.get("t") or {}).get("_ref") or "")
        for row in table_rows
        if (row.get("t") or {}).get("_ref")
    ]
    # Wide partitioned databases can have hundreds of thousands of columns.
    # Aggregate a bounded table batch per transaction instead of collecting
    # the entire database's columns in one Neo4j result.
    for refs in _chunks(table_refs, 100):
        column_rows = workspace.cypher(
            """
            UNWIND $table_refs AS table_ref
            MATCH (t:table {_ref: table_ref})
            OPTIONAL MATCH (t)--(c:col)
            WITH t, c
            ORDER BY t._ref, c.ordinal_position, c.name
            RETURN t._ref AS table_ref,
                   [name IN collect(c.name) WHERE name IS NOT NULL] AS column_names
            """,
            params={"table_refs": refs},
        )
        columns_by_table.update({
            str(row.get("table_ref") or ""): tuple(
                str(name) for name in (row.get("column_names") or []) if name
            )
            for row in column_rows
        })

    tables: list[TableInfo] = []
    for row in table_rows:
        db = row.get("d") or {}
        table = row.get("t") or {}
        db_ref = str(db.get("_ref") or db.get("name") or "")
        table_ref = str(table.get("_ref") or "")
        table_name = str(table.get("table_name") or table.get("name") or "")
        if not db_ref or not table_ref or not table_name:
            continue
        schema_ref = str(table.get("_schema_ref") or "")
        schema_name = str(table.get("schema_name") or _schema_name_from_ref(schema_ref) or "")
        if not schema_ref and schema_name:
            schema_ref = f"{db_ref}--{schema_name}"
        columns = columns_by_table.get(table_ref, ())
        family, pattern_types = _table_family(table_name)
        tables.append(TableInfo(
            db_ref=db_ref,
            table_ref=table_ref,
            schema_ref=schema_ref,
            database_name=str(table.get("database_name") or db.get("database_name") or db.get("name") or db_ref),
            schema_name=schema_name,
            table_name=table_name,
            family=family,
            pattern_types=pattern_types,
            columns=columns,
        ))
    return tables


def _schema_name_from_ref(schema_ref: str) -> str:
    parts = [part for part in str(schema_ref or "").split("--") if part]
    return parts[-1] if len(parts) >= 2 else ""


def _column_signature(columns: Iterable[str], *, ordered: bool) -> tuple[str, ...]:
    normalized = [str(col).lower() for col in columns]
    return tuple(normalized if ordered else sorted(normalized))


def _group_summary(key: tuple[str, str, str, str], members: list[TableInfo]) -> dict:
    db_ref, schema_ref, schema_name, family = key
    ordered_signatures = Counter(_column_signature(member.columns, ordered=True) for member in members)
    set_signatures = Counter(_column_signature(member.columns, ordered=False) for member in members)
    column_sets = [set(_column_signature(member.columns, ordered=False)) for member in members]
    common_columns = set.intersection(*column_sets) if column_sets else set()
    union_columns = set.union(*column_sets) if column_sets else set()
    common_column_names, variable_column_names = _column_name_summaries(members, common_columns, union_columns)
    representatives = _representative_members(members, same_order_hint=len(ordered_signatures) == 1)
    member_column_counts = Counter(len(member.columns) for member in members)
    pattern_counts = Counter(pattern for member in members for pattern in member.pattern_types)
    primary_pattern_type = _primary_pattern_type(pattern_counts, members)
    same_order = len(ordered_signatures) == 1
    same_set = len(set_signatures) == 1
    consistency = "same_order" if same_order else "same_set" if same_set else "drifting"
    sorted_members = sorted(members, key=lambda item: item.table_name)
    group_ref = _group_ref(db_ref, schema_name, family)
    display_scope = f"{schema_name}.{family}" if schema_name else family
    return {
        "name": f"table_group[{display_scope}]",
        "_ref": group_ref,
        "_db_ref": db_ref,
        "_schema_ref": schema_ref,
        "db_ref": db_ref,
        "schema_ref": schema_ref,
        "database_name": sorted_members[0].database_name if sorted_members else db_ref,
        "schema_name": schema_name,
        "family": family,
        "member_count": len(sorted_members),
        "members": [member.table_name for member in sorted_members],
        "member_refs": [member.table_ref for member in sorted_members],
        "sample_members": [member.table_name for member in sorted_members[:8]],
        "representative_member": representatives[0].table_name if representatives else "",
        "representative_member_ref": representatives[0].table_ref if representatives else "",
        "representative_members": [member.table_name for member in representatives],
        "representative_member_refs": [member.table_ref for member in representatives],
        "representative_column_signatures": [
            {
                "table": member.table_name,
                "column_count": len(member.columns),
                "columns": list(member.columns[:MAX_STORED_COLUMNS]),
                "truncated": len(member.columns) > MAX_STORED_COLUMNS,
            }
            for member in representatives
        ],
        "column_count_distribution": dict(sorted(member_column_counts.items())),
        "same_order_columns": same_order,
        "same_column_set": same_set,
        "column_set_signatures": len(set_signatures),
        "common_column_count": len(common_columns),
        "union_column_count": len(union_columns),
        "variable_column_count": len(union_columns - common_columns),
        "common_columns": common_column_names[:MAX_STORED_COLUMNS],
        "common_columns_truncated": len(common_column_names) > MAX_STORED_COLUMNS,
        "variable_columns": variable_column_names[:MAX_STORED_COLUMNS],
        "variable_columns_truncated": len(variable_column_names) > MAX_STORED_COLUMNS,
        "consistency": consistency,
        "pattern_types": [pattern for pattern, _ in pattern_counts.most_common()],
        "primary_pattern_type": primary_pattern_type,
        "cognitive_shape": _cognitive_shape(primary_pattern_type),
        "agent_usage_hint": _agent_usage_hint(primary_pattern_type, consistency),
        "schema_reading_strategy": _schema_reading_strategy(consistency, representatives),
        "grouping_method": "pattern_table_family_v1",
        "family_digest": hashlib.sha1(group_ref.encode("utf-8")).hexdigest()[:12],
        "labels": ["table_group"],
    }


def _column_name_summaries(
    members: list[TableInfo],
    common_columns: set[str],
    union_columns: set[str],
) -> tuple[list[str], list[str]]:
    display_by_normalized: dict[str, str] = {}
    first_member_order: list[str] = []
    for member in members:
        for column in member.columns:
            normalized = str(column).lower()
            display_by_normalized.setdefault(normalized, str(column))
            if normalized not in first_member_order:
                first_member_order.append(normalized)

    common = [display_by_normalized[name] for name in first_member_order if name in common_columns]
    variable = [display_by_normalized[name] for name in first_member_order if name in union_columns - common_columns]
    return common, variable


def _representative_members(members: list[TableInfo], *, same_order_hint: bool) -> list[TableInfo]:
    sorted_members = sorted(members, key=lambda item: item.table_name)
    if not sorted_members:
        return []

    by_signature: dict[tuple[str, ...], list[TableInfo]] = defaultdict(list)
    for member in sorted_members:
        signature = _column_signature(member.columns, ordered=same_order_hint)
        by_signature[signature].append(member)

    ranked = sorted(
        by_signature.values(),
        key=lambda group: (-len(group), group[0].table_name),
    )
    return [group[0] for group in ranked[:MAX_REPRESENTATIVE_MEMBERS]]


def _cognitive_shape(pattern_type: str) -> str:
    if pattern_type in {
        "date_shard_yyyymmdd",
        "month_shard_yyyymm",
        "year_shard_yyyy",
        "quarter_shard",
        "compact_year_suffix",
    }:
        return "time_partitioned_table_family"
    if pattern_type in {"release_version", "numeric_release_version"}:
        return "versioned_snapshot_family"
    if pattern_type == "chromosome_shard":
        return "domain_sharded_table_family"
    if pattern_type == "geo_region_shard":
        return "domain_sharded_table_family"
    if pattern_type == "numeric_suffix_shard":
        return "enumerated_shard_family"
    return "name_pattern_family"


def _agent_usage_hint(pattern_type: str, consistency: str) -> str:
    column_hint = (
        "columns are stable across members"
        if consistency in {"same_order", "same_set"}
        else "inspect common and variable columns before assuming all members share one schema"
    )
    if pattern_type in {"date_shard_yyyymmdd", "month_shard_yyyymm", "year_shard_yyyy", "quarter_shard"}:
        return f"Treat members as time partitions; read representative members plus date/suffix range; {column_hint}."
    if pattern_type == "compact_year_suffix":
        return f"Treat members as compact year/cycle suffix tables; decode the suffix before querying; {column_hint}."
    if pattern_type in {"release_version", "numeric_release_version"}:
        return f"Treat members as release snapshots; identify the relevant release before querying; {column_hint}."
    if pattern_type == "chromosome_shard":
        return f"Treat members as domain shards; select shards from the question or aggregate over all shards; {column_hint}."
    if pattern_type == "geo_region_shard":
        return f"Treat members as geographic region shards; select regions from the question or aggregate over all regions; {column_hint}."
    if pattern_type == "numeric_suffix_shard":
        return f"Treat members as enumerated physical shards; infer suffix semantics from table docs or sample names; {column_hint}."
    return f"Treat members as a named physical table family; verify semantics from member names; {column_hint}."


def _schema_reading_strategy(consistency: str, representatives: list[TableInfo]) -> str:
    if not representatives:
        return "No representative member available; inspect member tables directly."
    names = ", ".join(member.table_name for member in representatives)
    if consistency == "same_order":
        return f"Read representative table {representatives[0].table_name}; its ordered columns represent the group."
    if consistency == "same_set":
        return f"Read representative table {representatives[0].table_name}; column order may vary but the column set is stable."
    return f"Read representative tables {names}; compare common_columns and variable_columns before generalizing across the group."


def _primary_pattern_type(pattern_counts: Counter, members: list[TableInfo]) -> str:
    if not pattern_counts:
        return "name_pattern"
    primary = pattern_counts.most_common(1)[0][0]
    if primary == "numeric_suffix_shard" and _looks_like_numeric_release_sequence(members):
        return "numeric_release_version"
    return primary


def _looks_like_numeric_release_sequence(members: list[TableInfo]) -> bool:
    suffixes = []
    for member in members:
        match = re.search(r"_(\d{1,3})\b$", member.table_name.upper())
        if not match:
            return False
        suffixes.append(int(match.group(1)))
    if len(suffixes) < 5:
        return False
    unique_suffixes = sorted(set(suffixes))
    if len(unique_suffixes) != len(suffixes):
        return False
    is_contiguous = unique_suffixes == list(range(unique_suffixes[0], unique_suffixes[-1] + 1))
    return is_contiguous and unique_suffixes[0] >= 10


def _group_ref(db_ref: str, schema_name: str, family: str) -> str:
    safe_schema = schema_name or "_default"
    return f"{db_ref}--table_group--{safe_schema}--{family}"


def _delete_stale_groups(workspace: Workspace, *, grouping_method: str) -> None:
    _write_cypher(
        workspace,
        "MATCH (c:logical_col {grouping_method: $column_grouping_method, project: $project}) DETACH DELETE c",
        params={"column_grouping_method": "table_group_column_role_v1"},
    )
    _write_cypher(
        workspace,
        "MATCH (g:table_group {grouping_method: $grouping_method, project: $project}) DETACH DELETE g",
        params={"grouping_method": grouping_method},
    )


def _upsert_group(workspace: Workspace, summary: dict) -> None:
    member_refs = list(summary.pop("member_refs"))
    db_ref = summary["db_ref"]
    schema_ref = summary.get("schema_ref") or ""
    props = neo4j_props({k: v for k, v in summary.items() if k != "labels"})
    _write_cypher(
        workspace,
        """
        MATCH (db_node {_ref: $db_ref})
        MERGE (g:table_group {_ref: $ref})
        ON CREATE SET g.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)
        ON MATCH SET g.id = coalesce(g.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8))
        SET g += $props
        SET g.project = $project
        SET g.labels = reduce(acc = [], label IN coalesce(g.labels, []) + ['table_group'] |
            CASE WHEN label IN acc THEN acc ELSE acc + label END)
        MERGE (db_node)-[:RELATED_TO]->(g)
        """,
        params={"ref": summary["_ref"], "db_ref": db_ref, "props": props},
    )
    if schema_ref:
        _write_cypher(
            workspace,
            """
            MATCH (g:table_group {_ref: $ref})
            MATCH (s {_ref: $schema_ref})
            MERGE (s)-[:RELATED_TO]->(g)
            """,
            params={"ref": summary["_ref"], "schema_ref": schema_ref},
        )
    _write_cypher(
        workspace,
        """
        MATCH (t:table {project: $project})
        WHERE t._ref IN $member_refs
        WITH collect(t) AS tables
        MATCH (g:table_group {_ref: $ref})
        UNWIND tables AS t
        MERGE (t)-[:RELATED_TO]->(g)
        """,
        params={
            "ref": summary["_ref"],
            "member_refs": member_refs,
        },
    )
    _upsert_logical_columns(workspace, summary["_ref"])


def _upsert_logical_columns(workspace: Workspace, table_group_ref: str) -> None:
    """Materialize one logical column for each repeated member-table role."""

    table_rows = workspace.cypher(
        """
        MATCH (g:table_group)-[:RELATED_TO]-(t:table)
        WHERE g._ref = $group_ref
        RETURN coalesce(t._ref, t.path, t.name) AS table_ref
        ORDER BY table_ref
        """,
        params={"group_ref": table_group_ref},
    )
    rows: list[dict] = []
    for table_refs in _chunks(
        [str(row["table_ref"]) for row in table_rows if row.get("table_ref")],
        100,
    ):
        rows.extend(workspace.cypher(
            """
            UNWIND $table_refs AS table_ref
            MATCH (t:table {_ref: table_ref})-[:RELATED_TO]-(c:col)
            RETURN table_ref,
                   coalesce(c._ref, c.path, c.name) AS col_ref,
                   c.name AS column_name,
                   c.data_type AS data_type,
                   c.labels AS labels
            ORDER BY table_ref, c.ordinal_position, column_name
            """,
            params={"table_refs": table_refs},
        ))
    by_role: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        role = _logical_column_role(row.get("column_name") or "")
        if role and row.get("col_ref") and row.get("table_ref"):
            by_role[role].append(row)

    logical_rows: list[dict] = []
    member_edges: list[tuple[str, str]] = []
    for role, members in sorted(by_role.items()):
        table_refs = {str(member["table_ref"]) for member in members}
        column_refs = sorted({str(member["col_ref"]) for member in members})
        if len(table_refs) < 2 or len(column_refs) < 2:
            continue
        digest = hashlib.sha1("|".join(column_refs).encode("utf-8")).hexdigest()[:12]
        logical_ref = f"{table_group_ref}--logical_col--{role}--{digest}"
        types = sorted({
            str(member.get("data_type") or _column_type_label(member.get("labels")))
            for member in members
            if member.get("data_type") or member.get("labels")
        })
        props = neo4j_props({
            "_ref": logical_ref,
            "name": f"logical_col[{role}]",
            "role": role,
            "member_count": len(column_refs),
            "table_count": len(table_refs),
            "data_types": types,
            "grouping_method": "table_group_column_role_v1",
        })
        logical_rows.append({"ref": logical_ref, "props": props})
        member_edges.extend((logical_ref, column_ref) for column_ref in column_refs)

    for chunk in _chunks(logical_rows, 500):
        _write_cypher(
            workspace,
            """
            MATCH (g:table_group {_ref: $group_ref, project: $project})
            UNWIND $rows AS row
            CREATE (l:logical_col {_ref: row.ref, project: $project})
            SET l.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)
            SET l += row.props
            SET l.labels = reduce(acc = [], label IN coalesce(l.labels, []) + ['logical_col'] |
                CASE WHEN label IN acc THEN acc ELSE acc + label END)
            MERGE (g)-[:RELATED_TO]->(l)
            """,
            params={"group_ref": table_group_ref, "rows": chunk},
        )

    refs = sorted({ref for edge in member_edges for ref in edge})
    needed_refs = set(refs)
    element_ids: dict[str, str] = {}
    for label in ("logical_col", "col"):
        node_rows = _write_cypher(
            workspace,
            f"""
            MATCH (n:{label} {{project: $project}})
            RETURN n._ref AS ref, elementId(n) AS element_id
            """,
        )
        element_ids.update({
            str(row["ref"]): str(row["element_id"])
            for row in node_rows
            if str(row["ref"]) in needed_refs
        })

    edge_rows = [
        {"logical": element_ids[logical_ref], "column": element_ids[column_ref]}
        for logical_ref, column_ref in member_edges
        if logical_ref in element_ids and column_ref in element_ids
    ]
    for chunk in _chunks(edge_rows, 2000):
        _write_cypher(
            workspace,
            """
            UNWIND $edges AS edge
            MATCH (l) WHERE elementId(l) = edge.logical
            MATCH (c) WHERE elementId(c) = edge.column
            MERGE (c)-[:RELATED_TO]->(l)
            """,
            params={"edges": chunk},
        )


def _chunks(items: list, size: int):
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _logical_column_role(column_name: str) -> str:
    role = str(column_name or "").strip().lower()
    role = re.sub(r"[^a-z0-9]+", "_", role).strip("_")
    return role


def _column_type_label(labels) -> str:
    if isinstance(labels, str):
        labels = [labels]
    for label in labels or []:
        value = str(label or "")
        if value.lower() not in {"col", "grouped", "standalone"}:
            return value
    return ""


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
