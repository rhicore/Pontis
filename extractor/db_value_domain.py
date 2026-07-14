"""Online strategy for the unified column-domain extractor.

This module builds candidates only. Graph persistence and pipeline
registration belong to :mod:`extractor.db_column_domain`.
"""
from __future__ import annotations

import hashlib
import logging
import time
from array import array
from collections import Counter
from typing import Any

from extractor.utils.database_catalog import (
    column_type_from_labels,
    decode_jsonish,
    load_database_columns,
)
from extractor.utils.distinct_value_index import (
    load_cached_distinct_hashes,
    load_or_build_distinct_hashes,
    open_database,
)
from extractor.utils.domain_profile import domain_compatibility
from extractor.utils.online_value_domains import (
    OnlineValueDomain,
    OnlineValueDomainConfig,
    ValueColumn,
    build_online_value_domains,
)
from extractor.utils.overlap_candidates import _make_column_domain
from extractor.utils.overlap_options import KEYLIKE_TOKENS, OverlapOptions
from extractor.utils.overlap_value_matchers import build_snowflake_bounded_value_profiles
from extractor.utils.semantic_domain import classify_semantic_domain
from storage.workspace import Workspace


logger = logging.getLogger(__name__)
GROUPING_METHOD = "online_value_evidence_domain_v2"

_MEASURE_LIKE = {"measure"}
_PAYLOAD_LIKE = {"text_payload", "file_or_resource", "binary", "semi_structured", "geo"}
_KEY_LIKE = {"identifier", "categorical_key", "geographic_key"}
_GENERIC_DOMAIN_TOKENS = {"no", "ref", "reference", "value", "number", "num", "type", "name"}


def build_online_candidates_for_database(
    workspace: Workspace,
    *,
    db_ref: str,
    db_node: dict,
    db_connect,
    domain_config: OnlineValueDomainConfig,
    min_members: int,
    max_logical_members: int | None = None,
    value_read_method: str = "exact_distinct",
    value_read_options: OverlapOptions | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Build online-clustered candidates without deciding their graph type."""

    columns = load_database_columns(
        workspace,
        db_ref,
        exclude_logical_members=max_logical_members is not None,
    )
    physical_count_rows = workspace.cypher(
        """
        MATCH (c:col)
        WHERE c._db_ref = $db_ref
        RETURN count(c) AS physical_columns
        """,
        params={"db_ref": db_ref},
    )
    physical_column_count = int(
        (physical_count_rows[0].get("physical_columns") if physical_count_rows else 0) or 0
    )
    if physical_column_count < 2:
        return [], {"databases": 1, "physical_columns": physical_column_count}

    for column in columns:
        column["semantic_profile"] = _semantic_profile(column)
    logical_columns = _load_materialized_comparison_columns(
        workspace,
        db_ref,
        columns,
        max_logical_members=max_logical_members,
    )
    dialect = str(getattr(db_connect, "dialect", "") or db_node.get("dialect") or "sqlite").lower()
    value_columns = _load_value_columns(
        db_connect,
        dialect,
        logical_columns,
        max_logical_members=max_logical_members,
        value_read_method=value_read_method,
        value_read_options=value_read_options,
    )
    result = build_online_value_domains(
        _ordered_value_columns(value_columns),
        domain_config,
        compatible=_domain_is_compatible,
        minimum_overlap=_minimum_domain_overlap,
    )

    summaries: list[dict] = []
    member_edges = 0
    for domain in result.domains:
        if len(domain.members) < min_members:
            continue
        summary = _domain_summary(db_ref, domain, domain_config)
        summaries.append(summary)
        member_edges += summary["member_count"]

    stats = {
        "databases": 1,
        "physical_columns": physical_column_count,
        "logical_columns": len(logical_columns),
        "value_domains": len(summaries),
        "domain_member_edges": member_edges,
        "domain_comparisons": result.domain_comparisons,
        "anchor_comparisons": result.anchor_comparisons,
    }
    return summaries, stats


def _load_materialized_comparison_columns(
    workspace: Workspace,
    db_ref: str,
    physical_columns: list[dict],
    *,
    max_logical_members: int | None = None,
) -> list[dict]:
    """Return materialized logical columns plus uncovered physical columns."""

    physical_by_ref = {str(column["entity_name"]): column for column in physical_columns}
    rows = workspace.cypher(
        """
        MATCH (d:db)-[:RELATED_TO*1..3]-(g:table_group)-[:RELATED_TO]-(l:logical_col)
        WHERE d._ref = $db_ref OR d.name = $db_ref OR d.path = $db_ref
        RETURN DISTINCT l, g
        ORDER BY l._ref
        """,
        params={"db_ref": db_ref},
    )
    covered: set[str] = set()
    result: list[dict] = []
    sampled_members: dict[str, list[str]] = {}
    if max_logical_members:
        sampled_members = _load_sampled_logical_member_refs(
            workspace,
            [
                str((row.get("l") or {}).get("_ref") or "")
                for row in rows
                if (row.get("l") or {}).get("_ref")
            ],
            max_logical_members,
        )
    for row in rows:
        logical_node = row.get("l") or {}
        group_node = row.get("g") or {}
        logical_ref = str(logical_node.get("_ref") or logical_node.get("name") or "")
        if max_logical_members:
            all_member_refs = sampled_members.get(logical_ref, [])
        else:
            member_rows = workspace.cypher(
                """
                MATCH (l:logical_col {_ref: $logical_ref})-[:RELATED_TO]-(c:col)
                RETURN DISTINCT coalesce(c._ref, c.path, c.name) AS member_ref
                ORDER BY member_ref
                """,
                params={"logical_ref": logical_ref},
            )
            all_member_refs = [
                str(member_row["member_ref"])
                for member_row in member_rows
                if member_row.get("member_ref")
            ]
        covered.update(ref for ref in all_member_refs if ref in physical_by_ref)
        selected_refs = all_member_refs
        if max_logical_members:
            selected_refs = [
                member["entity_name"]
                for member in _evenly_spaced_members(
                    [{"entity_name": ref} for ref in all_member_refs],
                    max_logical_members,
                )
            ]
        selected_by_ref = dict(physical_by_ref)
        missing_refs = [ref for ref in selected_refs if ref not in selected_by_ref]
        if missing_refs:
            selected_by_ref.update(
                _load_physical_columns_by_ref(workspace, db_ref, missing_refs)
            )
        members = [
            selected_by_ref[ref]
            for ref in selected_refs
            if ref in selected_by_ref
        ]
        if len(members) < 2:
            continue
        group_ref = str(group_node.get("_ref") or group_node.get("name") or "")
        role = str(logical_node.get("role") or logical_node.get("name") or "logical_column")
        logical = _make_column_domain("table_group:" + group_ref, role, members, {group_ref})
        logical.update({
            "entity_name": logical_ref or logical["entity_name"],
            "column_ref": logical_ref or logical["column_ref"],
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


def _load_sampled_logical_member_refs(
    workspace: Workspace,
    logical_refs: list[str],
    limit: int,
) -> dict[str, list[str]]:
    """Sample partition members in bounded graph transactions."""

    result: dict[str, list[str]] = {}
    for offset in range(0, len(logical_refs), 16):
        rows = workspace.cypher(
            """
            MATCH (l:logical_col)-[:RELATED_TO]-(c:col)
            WHERE l._ref IN $logical_refs
            WITH l, c ORDER BY l._ref, c._ref
            WITH l, collect(coalesce(c._ref, c.path, c.name)) AS refs
            WITH l, refs,
                 range(0, CASE WHEN size(refs) < $limit
                               THEN size(refs) - 1 ELSE $limit - 1 END) AS indexes
            RETURN l._ref AS logical_ref,
                   [i IN indexes | refs[
                       CASE WHEN size(refs) <= $limit THEN i
                            ELSE toInteger(round(i * (size(refs) - 1.0) / ($limit - 1)))
                       END
                   ]] AS member_refs
            """,
            params={"logical_refs": logical_refs[offset:offset + 16], "limit": limit},
        )
        for row in rows:
            ref = str(row.get("logical_ref") or "")
            if ref:
                result[ref] = [str(item) for item in row.get("member_refs") or [] if item]
    return result


def _load_physical_columns_by_ref(
    workspace: Workspace,
    db_ref: str,
    refs: list[str],
) -> dict[str, dict]:
    rows = workspace.cypher(
        """
        MATCH (t)-[:RELATED_TO]-(c:col)
        WHERE (t:table OR t:view) AND c._ref IN $refs
        RETURN DISTINCT t, c
        """,
        params={"refs": refs},
    )
    result: dict[str, dict] = {}
    for row in rows:
        table = row.get("t") or {}
        col = row.get("c") or {}
        col_ref = str(col.get("_ref") or col.get("path") or "")
        table_ref = str(table.get("_ref") or table.get("path") or table.get("name") or "")
        if not col_ref or not table_ref:
            continue
        result[col_ref] = {
            "entity_name": col_ref,
            "db_ref": db_ref,
            "table": table_ref,
            "table_ref": table_ref,
            "table_name": str(table.get("table_name") or table.get("name") or ""),
            "schema_name": str(table.get("schema_name") or col.get("schema_name") or ""),
            "column": str(col.get("column_name") or col.get("name") or ""),
            "column_ref": col_ref,
            "data_type": str(col.get("data_type") or column_type_from_labels(col)),
            "cardinality": int(col.get("cardinality") or 0),
            "sample": decode_jsonish(col.get("sample"), default=[]),
            "topk": decode_jsonish(col.get("topk"), default=[]),
            "domain_profile": decode_jsonish(col.get("domain_profile"), default={}),
        }
    return result


def _load_value_columns(
    db_connect,
    dialect: str,
    logical_columns: list[dict],
    *,
    max_logical_members: int | None = None,
    value_read_method: str = "exact_distinct",
    value_read_options: OverlapOptions | None = None,
) -> list[ValueColumn]:
    selected_members: dict[str, dict] = {}
    members_by_logical: list[tuple[dict, list[dict]]] = []
    for logical in logical_columns:
        members = logical.get("domain_members") or [logical]
        if logical.get("domain_members") and max_logical_members:
            members = _evenly_spaced_members(members, max_logical_members)
        members_by_logical.append((logical, members))
        for member in members:
            selected_members[str(member["entity_name"])] = member

    if value_read_method == "snowflake_adaptive_probe":
        if dialect != "snowflake":
            raise ValueError("snowflake_adaptive_probe value reading requires Snowflake")
        physical_cache = _load_bounded_value_samples(
            db_connect,
            selected_members.values(),
            value_read_options or OverlapOptions(),
        )
    elif value_read_method == "exact_distinct":
        physical_cache = {}
        missing_members: list[dict] = []
        for ref, member in selected_members.items():
            cached = load_cached_distinct_hashes(member)
            if cached is None:
                missing_members.append(member)
            else:
                physical_cache[ref] = frozenset(cached)

        if missing_members:
            _fill_missing_value_indexes(
                db_connect,
                dialect,
                missing_members,
                physical_cache,
            )
        else:
            logger.info("  Loaded %d value columns entirely from cache", len(physical_cache))
    else:
        raise ValueError(f"Unsupported value_read_method: {value_read_method!r}")

    value_columns: list[ValueColumn] = []
    for logical, members in members_by_logical:
        values: set[int] = set()
        for member in members:
            values.update(physical_cache.get(str(member["entity_name"]), ()))
        if not values:
            continue
        profile = _semantic_profile(logical)
        metadata = {
            **logical,
            "semantic_profile": profile,
            "value_read_method": value_read_method,
        }
        value_columns.append(ValueColumn(
            ref=str(logical["entity_name"]),
            values=frozenset(values),
            bucket=str(logical.get("db_ref") or "").upper(),
            metadata=metadata,
        ))
    return value_columns


def _load_bounded_value_samples(
    db_connect,
    members,
    options: OverlapOptions,
) -> dict[str, frozenset[int]]:
    """Read bounded Snowflake row samples through the adaptive-probe cache."""

    connection = _open_value_database_with_retry(db_connect)
    try:
        cursor = connection.cursor()
        try:
            profiles = build_snowflake_bounded_value_profiles(cursor, members, options)
        finally:
            cursor.close()
    finally:
        connection.close()
    return {
        ref: frozenset(profile.sample_hashes)
        for ref, profile in profiles.items()
    }

def _fill_missing_value_indexes(
    db_connect,
    dialect: str,
    missing_members: list[dict],
    physical_cache: dict[str, frozenset[int]],
) -> None:
    """Fetch only cache misses and add them to ``physical_cache``."""

    logger.info(
        "  Value index cache: %d hits, %d misses",
        len(physical_cache),
        len(missing_members),
    )
    connection = _open_value_database_with_retry(db_connect)
    try:
        cursor = connection.cursor()
        try:
            inaccessible_refs: list[str] = []
            inaccessible_relations: set[str] = set()
            for member in missing_members:
                ref = str(member["entity_name"])
                relation_ref = str(member.get("table_ref") or member.get("table") or "")
                if relation_ref and relation_ref in inaccessible_relations:
                    inaccessible_refs.append(ref)
                    physical_cache[ref] = frozenset()
                    continue
                try:
                    hashes: array = load_or_build_distinct_hashes(cursor, member, dialect)
                    physical_cache[ref] = frozenset(hashes)
                except Exception as exc:
                    inaccessible_refs.append(ref)
                    logger.debug("Skipping inaccessible value column %s: %s", ref, exc)
                    if relation_ref and _relation_is_inaccessible(exc):
                        inaccessible_relations.add(relation_ref)
                    physical_cache[ref] = frozenset()
            if inaccessible_refs:
                examples = ", ".join(inaccessible_refs[:3])
                logger.warning(
                    "Skipped %d inaccessible value columns (examples: %s)",
                    len(inaccessible_refs),
                    examples,
                )
        finally:
            cursor.close()
    finally:
        connection.close()


def _open_value_database_with_retry(db_connect, *, attempts: int = 4):
    """Open a value-source connection across transient network failures."""

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return open_database(db_connect, readonly=True)
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                raise
            delay = min(10, 2 ** attempt)
            logger.warning(
                "Value-source connection failed (%d/%d); retrying in %ds: %s",
                attempt,
                attempts,
                delay,
                str(exc).splitlines()[0],
            )
            time.sleep(delay)
    raise last_exc or RuntimeError("Could not open value-source connection")


def _relation_is_inaccessible(exc: Exception) -> bool:
    """Return whether one failure proves the whole table/view is unavailable."""

    message = str(exc).lower()
    return "does not exist or not authorized" in message


def _evenly_spaced_members(members: list[dict], limit: int) -> list[dict]:
    """Choose deterministic representatives across a partition family."""

    ordered = sorted(members, key=lambda member: str(member.get("entity_name") or ""))
    if limit <= 0 or len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[len(ordered) // 2]]
    indexes = {
        round(index * (len(ordered) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [ordered[index] for index in sorted(indexes)]


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
    value_read_methods = sorted({
        str(column.metadata.get("value_read_method") or "exact_distinct")
        for column in domain.members
    })
    return {
        "_ref": domain_ref,
        "name": f"value_domain_{digest}",
        "db_ref": db_ref,
        "schema_name": schema_name,
        "schema_ref": str(domain.members[0].metadata.get("_schema_ref") or f"{db_ref}--{schema_name}"),
        "member_refs": member_refs,
        "member_count": len(member_refs),
        "union_cardinality": len(domain.union_values),
        "semantic_roles": dict(sorted(roles.items())),
        "grouping_method": GROUPING_METHOD,
        "extraction_method": GROUPING_METHOD,
        "value_read_method": value_read_methods[0] if len(value_read_methods) == 1 else value_read_methods,
        "overlap_metric": "intersection_over_min_cardinality",
        "overlap_threshold": config.overlap_threshold,
        "anchor_overlap_threshold": config.anchor_overlap_threshold or config.overlap_threshold,
        "min_anchor_support": config.min_anchor_support,
        "review_status": "pending_review",
        "extraction_evidence": domain.assignments,
    }
