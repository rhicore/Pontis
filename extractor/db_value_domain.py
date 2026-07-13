"""Storage-backed extractor for shared column value-domain entities."""
from __future__ import annotations

import hashlib
import logging
from array import array
from collections import Counter
from typing import Any

from extractor.db_column_overlap import _load_db_columns
from extractor.utils.domain_profile import domain_compatibility
from extractor.utils.online_value_domains import (
    OnlineValueDomain,
    OnlineValueDomainConfig,
    ValueColumn,
    build_online_value_domains,
)
from extractor.utils.overlap_candidates import _make_column_domain
from extractor.utils.overlap_options import KEYLIKE_TOKENS
from extractor.utils.overlap_value_matchers import (
    _load_or_build_column_hash_index,
    _open_db_connection,
)
from extractor.utils.refs import neo4j_props
from extractor.utils.semantic_domain import classify_semantic_domain
from storage.workspace import Workspace


logger = logging.getLogger(__name__)
GROUPING_METHOD = "online_distinct_value_domain_v1"

_MEASURE_LIKE = {"measure"}
_PAYLOAD_LIKE = {"text_payload", "file_or_resource", "binary", "semi_structured", "geo"}
_KEY_LIKE = {"identifier", "categorical_key", "geographic_key"}
_GENERIC_DOMAIN_TOKENS = {"no", "ref", "reference", "value", "number", "num", "type", "name"}


def generate(
    workspace: Workspace,
    config=None,
    *,
    overlap_threshold: float = 0.5,
    anchor_overlap_threshold: float | None = None,
    min_anchor_support: float = 0.75,
    max_anchors: int = 8,
    min_members: int = 2,
) -> dict[str, int]:
    """Create value-domain nodes without materializing pairwise overlaps."""

    del config
    logger.info("=== DB value domain extraction ===")
    totals = Counter()
    rows = workspace.cypher(
        """
        MATCH (d:db)
        WITH d, coalesce(d._db_connect, d.db_connect) AS db_connect
        WHERE (d._ref IS NOT NULL OR d.name IS NOT NULL) AND db_connect IS NOT NULL
        RETURN d, db_connect
        ORDER BY coalesce(d._ref, d.name)
        """
    )
    for row in rows:
        db_node = row.get("d") or {}
        db_ref = str(db_node.get("_ref") or db_node.get("path") or db_node.get("name") or "")
        db_connect = row.get("db_connect") or db_node.get("_db_connect") or db_node.get("db_connect")
        if not db_ref or not callable(db_connect):
            continue
        try:
            stats = _generate_for_database(
                workspace,
                db_ref=db_ref,
                db_node=db_node,
                db_connect=db_connect,
                domain_config=OnlineValueDomainConfig(
                    overlap_threshold=overlap_threshold,
                    match_policy="union_and_anchor",
                    anchor_overlap_threshold=anchor_overlap_threshold,
                    min_anchor_support=min_anchor_support,
                    max_anchors=max_anchors,
                ),
                min_members=max(2, min_members),
            )
            totals.update(stats)
        except Exception as exc:
            totals["database_errors"] += 1
            logger.warning("Failed to generate value domains for %s: %s", db_ref, exc)
    logger.info("  Value domain totals: %s", dict(totals))
    return dict(totals)


def _generate_for_database(
    workspace: Workspace,
    *,
    db_ref: str,
    db_node: dict,
    db_connect,
    domain_config: OnlineValueDomainConfig,
    min_members: int,
) -> dict[str, int]:
    columns = _load_db_columns(workspace, db_ref)
    if len(columns) < 2:
        return {"databases": 1, "physical_columns": len(columns)}

    for column in columns:
        column["semantic_profile"] = _semantic_profile(column)
    logical_columns = _load_materialized_comparison_columns(workspace, db_ref, columns)
    dialect = str(getattr(db_connect, "dialect", "") or db_node.get("dialect") or "sqlite").lower()
    value_columns = _load_value_columns(db_connect, dialect, logical_columns)
    result = build_online_value_domains(
        _ordered_value_columns(value_columns),
        domain_config,
        compatible=_domain_is_compatible,
        minimum_overlap=_minimum_domain_overlap,
    )

    written = 0
    member_edges = 0
    active_refs: list[str] = []
    for domain in result.domains:
        if len(domain.members) < min_members:
            continue
        summary = _domain_summary(db_ref, domain, domain_config)
        active_refs.append(summary["_ref"])
        _upsert_domain(workspace, summary)
        written += 1
        member_edges += summary["member_count"]
    _delete_stale_domains(workspace, db_ref, active_refs)

    stats = {
        "databases": 1,
        "physical_columns": len(columns),
        "logical_columns": len(logical_columns),
        "value_domains": written,
        "domain_member_edges": member_edges,
        "domain_comparisons": result.domain_comparisons,
        "anchor_comparisons": result.anchor_comparisons,
    }
    logger.info("  %s value domains: %s", db_ref, stats)
    return stats


def _load_materialized_comparison_columns(
    workspace: Workspace,
    db_ref: str,
    physical_columns: list[dict],
) -> list[dict]:
    """Return materialized logical columns plus uncovered physical columns."""

    physical_by_ref = {str(column["entity_name"]): column for column in physical_columns}
    rows = workspace.cypher(
        """
        MATCH (d:db)-[:RELATED_TO*1..3]-(g:table_group)-[:RELATED_TO]-(l:logical_col)
        WHERE d._ref = $db_ref OR d.name = $db_ref OR d.path = $db_ref
        MATCH (l)-[:RELATED_TO]-(c:col)
        RETURN l, g, collect(DISTINCT coalesce(c._ref, c.path, c.name)) AS member_refs
        ORDER BY l._ref
        """,
        params={"db_ref": db_ref},
    )
    covered: set[str] = set()
    result: list[dict] = []
    for row in rows:
        logical_node = row.get("l") or {}
        group_node = row.get("g") or {}
        members = [
            physical_by_ref[ref]
            for raw_ref in row.get("member_refs") or []
            if (ref := str(raw_ref)) in physical_by_ref
        ]
        if len(members) < 2:
            continue
        covered.update(str(member["entity_name"]) for member in members)
        group_ref = str(group_node.get("_ref") or group_node.get("name") or "")
        role = str(logical_node.get("role") or logical_node.get("name") or "logical_column")
        logical = _make_column_domain("table_group:" + group_ref, role, members, {group_ref})
        logical.update({
            "entity_name": str(logical_node.get("_ref") or logical_node.get("name") or logical["entity_name"]),
            "column_ref": str(logical_node.get("_ref") or logical_node.get("name") or logical["column_ref"]),
            "table": group_ref,
            "table_ref": group_ref,
            "table_name": str(group_node.get("name") or group_node.get("family") or group_ref),
            "schema_name": str(group_node.get("schema_name") or logical.get("schema_name") or ""),
            "logical_col": True,
        })
        result.append(logical)

    result.extend(
        column for ref, column in physical_by_ref.items()
        if ref not in covered
    )
    result.sort(key=lambda column: str(column.get("entity_name") or ""))
    return result


def _load_value_columns(db_connect, dialect: str, logical_columns: list[dict]) -> list[ValueColumn]:
    connection = _open_db_connection(db_connect, readonly=True)
    try:
        cursor = connection.cursor()
        try:
            physical_cache: dict[str, frozenset[int]] = {}
            value_columns: list[ValueColumn] = []
            for logical in logical_columns:
                values: set[int] = set()
                members = logical.get("domain_members") or [logical]
                for member in members:
                    ref = str(member["entity_name"])
                    if ref not in physical_cache:
                        hashes: array = _load_or_build_column_hash_index(cursor, member, dialect)
                        physical_cache[ref] = frozenset(hashes)
                    values.update(physical_cache[ref])
                if not values:
                    continue
                profile = _semantic_profile(logical)
                metadata = {**logical, "semantic_profile": profile}
                value_columns.append(ValueColumn(
                    ref=str(logical["entity_name"]),
                    values=frozenset(values),
                    bucket=str(logical.get("db_ref") or "").upper(),
                    metadata=metadata,
                ))
            return value_columns
        finally:
            cursor.close()
    finally:
        connection.close()


def _ordered_value_columns(columns: list[ValueColumn]) -> list[ValueColumn]:
    """Large, semantically specific columns seed domains deterministically."""

    return sorted(
        columns,
        key=lambda column: (
            -_semantic_specificity(column.metadata.get("semantic_profile") or {}),
            -len(column.values),
            column.ref,
        ),
    )


def _semantic_specificity(profile: dict) -> int:
    role = str(profile.get("primary_role") or "unknown")
    if role in _KEY_LIKE:
        return 3
    if role in {"temporal_key", "categorical", "name"}:
        return 2
    if role in _MEASURE_LIKE | _PAYLOAD_LIKE:
        return 1
    return 0


def _domain_is_compatible(column: ValueColumn, domain: OnlineValueDomain) -> bool:
    """Reject only strong semantic/representation contradictions before values."""

    candidate = column.metadata
    for anchor in domain.anchors:
        anchor_metadata = anchor.metadata
        shared_tokens = _shared_entity_tokens(column, anchor)
        strong_semantics = _strong_semantic_match(column, anchor)
        compatible, _reason, _evidence = domain_compatibility(candidate, anchor_metadata)
        if not compatible and not strong_semantics:
            return False
        if _semantic_hard_conflict(
            candidate.get("semantic_profile") or {},
            anchor_metadata.get("semantic_profile") or {},
        ) and not (shared_tokens and _different_schema(column, anchor)):
            return False
        if _cross_schema_weak_match(column, anchor, shared_tokens):
            return False
        if _weak_key_alias(column, anchor, strong_semantics):
            return False
    return True


def _semantic_hard_conflict(left: dict, right: dict) -> bool:
    left_role = str(left.get("primary_role") or "unknown")
    right_role = str(right.get("primary_role") or "unknown")
    roles = {left_role, right_role}
    left_semantics = set(left.get("semantic_domains") or []) - {"unclassified"}
    right_semantics = set(right.get("semantic_domains") or []) - {"unclassified"}
    left_tokens = _profile_entity_tokens(left)
    right_tokens = _profile_entity_tokens(right)
    if _profiles_strong_semantic_match(left, right):
        return False
    if left_role == right_role == "measure":
        return bool(left_semantics and right_semantics and not (left_semantics & right_semantics))
    if left_role == right_role == "unknown" and left_tokens and right_tokens:
        return not bool(left_tokens & right_tokens)
    if left_role == right_role:
        return False
    if roles & _MEASURE_LIKE and roles - _MEASURE_LIKE:
        return True
    if roles & _PAYLOAD_LIKE and roles - _PAYLOAD_LIKE:
        return True
    if "temporal_key" in roles:
        return True
    if "unknown" in roles and roles & {"categorical", "name"}:
        return not bool(left_tokens & right_tokens)

    return False


def _minimum_domain_overlap(left: ValueColumn, right: ValueColumn) -> float:
    """Translate semantic strength into the required overlap/min evidence."""

    if _strong_semantic_match(left, right) or _same_column_role(left, right):
        return 0.0
    if _shared_entity_tokens(left, right) and _different_schema(left, right):
        return 0.0
    left_role = str((left.metadata.get("semantic_profile") or {}).get("primary_role") or "unknown")
    right_role = str((right.metadata.get("semantic_profile") or {}).get("primary_role") or "unknown")
    if left_role in _KEY_LIKE and right_role in _KEY_LIKE:
        return 0.3
    return 0.5


def _cross_schema_weak_match(left: ValueColumn, right: ValueColumn, shared_tokens: set[str]) -> bool:
    left_schema = str(left.metadata.get("schema_name") or "").upper()
    right_schema = str(right.metadata.get("schema_name") or "").upper()
    if not left_schema or not right_schema or left_schema == right_schema or shared_tokens:
        return False
    return _value_jaccard(left, right) < 0.9


def _weak_key_alias(left: ValueColumn, right: ValueColumn, strong_semantics: bool) -> bool:
    if strong_semantics or _same_column_role(left, right):
        return False
    left_role = str((left.metadata.get("semantic_profile") or {}).get("primary_role") or "unknown")
    right_role = str((right.metadata.get("semantic_profile") or {}).get("primary_role") or "unknown")
    roles = {left_role, right_role}
    if left_role in _KEY_LIKE and right_role in _KEY_LIKE:
        return _value_jaccard(left, right) < 0.05
    if roles & _KEY_LIKE and roles & {"unknown", "categorical", "name"}:
        return _value_jaccard(left, right) < 0.5
    return False


def _value_jaccard(left: ValueColumn, right: ValueColumn) -> float:
    union = len(left.values | right.values)
    return len(left.values & right.values) / union if union else 0.0


def _shared_entity_tokens(left: ValueColumn, right: ValueColumn) -> set[str]:
    left_tokens = _profile_entity_tokens(left.metadata.get("semantic_profile") or {})
    right_tokens = _profile_entity_tokens(right.metadata.get("semantic_profile") or {})
    return left_tokens & right_tokens


def _strong_semantic_match(left: ValueColumn, right: ValueColumn) -> bool:
    if not _shared_entity_tokens(left, right):
        return False
    left_profile = left.metadata.get("semantic_profile") or {}
    right_profile = right.metadata.get("semantic_profile") or {}
    return _profiles_strong_semantic_match(left_profile, right_profile)


def _profiles_strong_semantic_match(left: dict, right: dict) -> bool:
    if not (_profile_entity_tokens(left) & _profile_entity_tokens(right)):
        return False
    left_role = str(left.get("primary_role") or "unknown")
    right_role = str(right.get("primary_role") or "unknown")
    if left_role == right_role:
        if left_role != "measure":
            return True
        left_semantics = set(left.get("semantic_domains") or []) - {"unclassified"}
        right_semantics = set(right.get("semantic_domains") or []) - {"unclassified"}
        return not left_semantics or not right_semantics or bool(left_semantics & right_semantics)
    roles = {left_role, right_role}
    if roles <= _KEY_LIKE:
        return True
    return False


def _profile_entity_tokens(profile: dict) -> set[str]:
    return set(profile.get("entity_tokens") or []) - _GENERIC_DOMAIN_TOKENS


def _different_schema(left: ValueColumn, right: ValueColumn) -> bool:
    left_schema = str(left.metadata.get("schema_name") or "").upper()
    right_schema = str(right.metadata.get("schema_name") or "").upper()
    return bool(left_schema and right_schema and left_schema != right_schema)


def _same_column_role(left: ValueColumn, right: ValueColumn) -> bool:
    left_name = _normalise_role(left.metadata.get("column") or "")
    right_name = _normalise_role(right.metadata.get("column") or "")
    return bool(left_name and left_name == right_name)


def _semantic_profile(column: dict) -> dict:
    profile = classify_semantic_domain(
        column.get("column", ""),
        column.get("data_type"),
        sample_values=column.get("sample") or [],
        domain_profile=column.get("domain_profile"),
    )
    tokens = set(profile.get("entity_tokens") or [])
    if not (tokens - _GENERIC_DOMAIN_TOKENS):
        tokens.update(_table_entity_tokens(column.get("table_name") or ""))
    return {**profile, "entity_tokens": sorted(tokens)}


def _table_entity_tokens(table_name: str) -> set[str]:
    compact = _normalise_role(table_name).replace("_", "")
    words = set(_normalise_role(table_name).split("_")) - {"table", "data", "fact", "dim"}
    words.update(
        token for token in KEYLIKE_TOKENS
        if len(token) >= 4 and token in compact
    )
    return {
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in words
        if word
    }


def _normalise_role(value: Any) -> str:
    import re

    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _domain_summary(db_ref: str, domain: OnlineValueDomain, config: OnlineValueDomainConfig) -> dict:
    member_refs = sorted({column.ref for column in domain.members})
    schema_name = str(domain.members[0].metadata.get("schema_name") or "")
    digest = hashlib.sha1("|".join(member_refs).encode("utf-8")).hexdigest()[:12]
    safe_schema = schema_name or "default"
    domain_ref = f"{db_ref}--value_domain--{safe_schema}--{digest}"
    roles = Counter(
        str((column.metadata.get("semantic_profile") or {}).get("primary_role") or "unknown")
        for column in domain.members
    )
    names = [str(column.metadata.get("column") or column.ref) for column in domain.members]
    return {
        "_ref": domain_ref,
        "name": f"value_domain[{safe_schema}:{names[0]}]",
        "db_ref": db_ref,
        "schema_name": schema_name,
        "schema_ref": str(domain.members[0].metadata.get("_schema_ref") or f"{db_ref}--{schema_name}"),
        "member_refs": member_refs,
        "member_count": len(member_refs),
        "union_cardinality": len(domain.union_values),
        "semantic_roles": dict(sorted(roles.items())),
        "grouping_method": GROUPING_METHOD,
        "overlap_metric": "intersection_over_min_cardinality",
        "overlap_threshold": config.overlap_threshold,
        "anchor_overlap_threshold": config.anchor_overlap_threshold or config.overlap_threshold,
        "min_anchor_support": config.min_anchor_support,
        "review_status": "pending_review",
        "extraction_evidence": domain.assignments,
    }


def _delete_stale_domains(workspace: Workspace, db_ref: str, active_refs: list[str]) -> None:
    _write_cypher(
        workspace,
        """
        MATCH (d:value_domain {project: $project, grouping_method: $grouping_method, db_ref: $db_ref})
        WHERE NOT d._ref IN $active_refs
        DETACH DELETE d
        """,
        params={
            "db_ref": db_ref,
            "grouping_method": GROUPING_METHOD,
            "active_refs": active_refs,
        },
    )


def _upsert_domain(workspace: Workspace, summary: dict) -> None:
    member_refs = summary.pop("member_refs")
    props = neo4j_props(summary)
    refresh_props = {
        key: value
        for key, value in props.items()
        if key not in {"review_status", "brief", "detail"}
    }
    _write_cypher(
        workspace,
        """
        MATCH (db:db {project: $project})
        WHERE db._ref = $db_ref OR db.name = $db_ref OR db.path = $db_ref
        MERGE (d:value_domain:domain {_ref: $ref, project: $project})
        ON CREATE SET d.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8),
                      d += $props
        ON MATCH SET d += $refresh_props
        SET d.labels = reduce(acc = [], label IN coalesce(d.labels, []) + ['value_domain', 'domain'] |
            CASE WHEN label IN acc THEN acc ELSE acc + label END)
        MERGE (db)-[:RELATED_TO]->(d)
        """,
        params={
            "db_ref": summary["db_ref"],
            "ref": summary["_ref"],
            "props": props,
            "refresh_props": refresh_props,
        },
    )
    schema_ref = summary.get("schema_ref")
    if schema_ref:
        _write_cypher(
            workspace,
            """
            MATCH (d:value_domain {_ref: $ref, project: $project})
            MATCH (s:schema {project: $project})
            WHERE s._ref = $schema_ref
            MERGE (s)-[:RELATED_TO]->(d)
            """,
            params={"ref": summary["_ref"], "schema_ref": schema_ref},
        )
    _write_cypher(
        workspace,
        """
        UNWIND $member_refs AS member_ref
        MATCH (d:value_domain {_ref: $ref, project: $project})
        MATCH (c {project: $project})
        WHERE (c:col OR c:logical_col)
          AND (c._ref = member_ref OR c.path = member_ref)
        MERGE (c)-[:RELATED_TO]->(d)
        """,
        params={"ref": summary["_ref"], "member_refs": member_refs},
    )


def _write_cypher(workspace: Workspace, query: str, params: dict | None = None) -> list:
    params = dict(params or {})
    rows: list[Any] = []
    for project in workspace.active_projects:
        store = workspace._get_store(project)
        if store is None:
            continue
        scoped = {**params, "project": project}
        with store.execution_lock:
            rows.extend(store.execute_cypher(query, params=scoped))
    return rows
