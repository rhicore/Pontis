"""Cheap candidate generation and pre-value filters for column overlap."""
from __future__ import annotations

import re
import hashlib
from collections import defaultdict
from itertools import combinations
from typing import Any, Callable, Dict, Iterable

from extractor.utils.overlap_options import (
    COLUMN_ROLE_SLOT_RE,
    GENERIC_KEY_TOKENS,
    KEYLIKE_SUFFIXES,
    KEYLIKE_TOKENS,
    MIN_PATTERN_TABLE_DOMAIN_ROLES,
    NUMERIC_TYPES,
    OverlapOptions,
    STOP_TOKENS,
    TEMPORAL_TYPES,
    CandidateFilterContext,
)
from extractor.utils.domain_profile import domain_compatibility, merge_domain_profiles


def _tokens(text: str) -> set[str]:
    if not text:
        return set()
    spaced = re.sub(r"^([A-Z]{2})([a-z])", r"\1 \2", text)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", spaced)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    spaced = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", spaced)
    raw = re.split(r"[^A-Za-z0-9]+", spaced.lower())
    tokens: set[str] = set()
    for token in raw:
        if not token or token in STOP_TOKENS:
            continue
        tokens.add(token)
        if token.startswith("adm"):
            tokens.add("adm")
            tokens.add("admin")
        if token.startswith("enroll"):
            tokens.add("enroll")
            tokens.add("enrollment")
        if token in {"pct", "percentage"}:
            tokens.add("percent")
        if token in {"desc", "description"}:
            tokens.add("description")
        if token in {"num", "number"}:
            tokens.add("number")
    return tokens


def _same_table_group(table1: str, table2: str, memberships: dict[str, set[str]]) -> bool:
    groups1 = memberships.get(str(table1), set())
    groups2 = memberships.get(str(table2), set())
    return bool(groups1 and groups2 and groups1 & groups2)


def _empty_candidate_stats() -> dict:
    return {
        "candidate_pairs": 0,
        "raw_pairs": 0,
        "column_domain_count": 0,
        "physical_column_count": 0,
        "multi_column_domain_count": 0,
        "pattern_table_domain_count": 0,
        "multi_table_pattern_domain_count": 0,
        "same_schema_skipped": 0,
        "same_group_skipped": 0,
        "same_table_skipped": 0,
        "same_table_group_duplicate_skipped": 0,
        "type_skipped": 0,
        "key_like_skipped": 0,
        "name_token_skipped": 0,
        "repeated_key_name_skipped": 0,
        "shape_skipped": 0,
        "top_k_skipped": 0,
        "exceeded": False,
    }


def _collect_pipeline_candidate_pairs(
    table_columns: dict[str, list[Dict]],
    table_group_memberships: dict[str, set[str]],
    *,
    options: OverlapOptions,
) -> tuple[list[tuple[Dict, Dict]], dict]:
    """Enumerate comparison units without applying any candidate filters.

    Table/column-domain compaction happens before this point because it changes
    the unit being compared. Every later decision is made by an explicit
    thresholded pipeline stage, so this function deliberately does not apply
    schema, name, type, shape, or value rules.
    """

    units_by_table, stats = _prepare_pipeline_comparison_units(
        table_columns,
        table_group_memberships,
        options=options,
    )
    candidates = list(_iter_pipeline_candidate_pairs(units_by_table))
    stats["raw_pairs"] = len(candidates)
    stats["candidate_pairs"] = len(candidates)
    return candidates, stats


def _prepare_pipeline_comparison_units(
    table_columns: dict[str, list[Dict]],
    table_group_memberships: dict[str, set[str]],
    *,
    options: OverlapOptions,
) -> tuple[dict[str, list[Dict]], dict]:
    """Create physical or logical column units before pair enumeration."""

    stats = _empty_candidate_stats()
    if options.column_domain_enabled:
        domains, domain_stats = _build_column_domains(table_columns, table_group_memberships, options)
        stats.update(domain_stats)
        units_by_table: dict[str, list[Dict]] = defaultdict(list)
        for domain in domains:
            units_by_table[domain["table"]].append(domain)
    else:
        units_by_table = table_columns
        stats["physical_column_count"] = sum(len(columns) for columns in table_columns.values())
        stats["column_domain_count"] = stats["physical_column_count"]
    return units_by_table, stats


def _iter_pipeline_candidate_pairs(
    units_by_table: dict[str, list[Dict]],
) -> Iterable[tuple[Dict, Dict]]:
    """Yield comparison pairs without retaining the full Cartesian product."""

    table_refs = sorted(units_by_table)
    for index, table1 in enumerate(table_refs):
        cols1 = units_by_table[table1]
        for table2 in table_refs[index + 1:]:
            for col1 in cols1:
                for col2 in units_by_table[table2]:
                    yield col1, col2
    for columns in units_by_table.values():
        yield from combinations(columns, 2)


def _collect_value_candidate_pairs(
    table_columns: dict[str, list[Dict]],
    table_group_memberships: dict[str, set[str]],
    *,
    options: OverlapOptions,
) -> tuple[list[tuple[Dict, Dict]], dict]:
    """Collect KG/profile-filtered candidates before touching the database."""
    if options.column_domain_enabled:
        return _collect_domain_value_candidate_pairs(table_columns, table_group_memberships, options=options)

    raw_candidates: list[tuple[Dict, Dict, float]] = []
    stats = _empty_candidate_stats()
    repeated_key_names = _repeated_key_names(table_columns.values())

    def add_pair(col1: Dict, col2: Dict) -> bool:
        stats["raw_pairs"] += 1
        ok, reason = _should_keep_value_candidate(col1, col2, options, repeated_key_names)
        if not ok:
            if reason in stats:
                stats[reason] += 1
            return True
        raw_candidates.append((col1, col2, _candidate_score(col1, col2, options)))
        return True

    table_refs = sorted(table_columns)
    for index, table1 in enumerate(table_refs):
        cols1 = table_columns[table1]
        for table2 in table_refs[index + 1:]:
            if options.skip_same_table_group and _same_table_group(table1, table2, table_group_memberships):
                stats["same_group_skipped"] += len(cols1) * len(table_columns[table2])
                continue
            for col1 in cols1:
                for col2 in table_columns[table2]:
                    if not add_pair(col1, col2):
                        return _finalize_candidates(raw_candidates, stats, options)

    if not options.same_table_overlap_enabled:
        for cols in table_columns.values():
            stats["same_table_skipped"] += len(cols) * (len(cols) - 1) // 2
        return _finalize_candidates(raw_candidates, stats, options)

    representative_tables = _table_group_representative_tables(table_columns.keys(), table_group_memberships)
    # 同表列也可能共享值域并造成字段混用，例如 city/district。对同一 table_group
    # 的物理分区表只检查一个代表表，避免重复扫描同构分区。
    for table, cols in table_columns.items():
        if (
            options.same_table_group_representative_only
            and not _is_same_table_overlap_representative(table, representative_tables, table_group_memberships)
        ):
            stats["same_table_group_duplicate_skipped"] += len(cols) * (len(cols) - 1) // 2
            continue
        for col1, col2 in combinations(cols, 2):
            if not add_pair(col1, col2):
                return _finalize_candidates(raw_candidates, stats, options)

    return _finalize_candidates(raw_candidates, stats, options)


def _collect_domain_value_candidate_pairs(
    table_columns: dict[str, list[Dict]],
    table_group_memberships: dict[str, set[str]],
    *,
    options: OverlapOptions,
) -> tuple[list[tuple[Dict, Dict]], dict]:
    """Collect candidates over logical column domains instead of physical columns."""

    domains, domain_stats = _build_column_domains(table_columns, table_group_memberships, options)
    domain_columns: dict[str, list[Dict]] = defaultdict(list)
    for domain in domains:
        domain_columns[domain["table"]].append(domain)

    raw_candidates: list[tuple[Dict, Dict, float]] = []
    stats = _empty_candidate_stats()
    stats.update(domain_stats)
    repeated_key_names = _repeated_key_names(domain_columns.values())

    def add_pair(col1: Dict, col2: Dict) -> bool:
        stats["raw_pairs"] += 1
        ok, reason = _should_keep_value_candidate(col1, col2, options, repeated_key_names)
        if not ok:
            if reason in stats:
                stats[reason] += 1
            return True
        raw_candidates.append((col1, col2, _candidate_score(col1, col2, options)))
        return True

    table_refs = sorted(domain_columns)
    for index, table1 in enumerate(table_refs):
        cols1 = domain_columns[table1]
        for table2 in table_refs[index + 1:]:
            for col1 in cols1:
                for col2 in domain_columns[table2]:
                    if not add_pair(col1, col2):
                        return _finalize_candidates(raw_candidates, stats, options)

    if not options.same_table_overlap_enabled:
        for cols in domain_columns.values():
            stats["same_table_skipped"] += len(cols) * (len(cols) - 1) // 2
        return _finalize_candidates(raw_candidates, stats, options)

    for cols in domain_columns.values():
        for col1, col2 in combinations(cols, 2):
            if not add_pair(col1, col2):
                return _finalize_candidates(raw_candidates, stats, options)

    return _finalize_candidates(raw_candidates, stats, options)


def _finalize_candidates(
    raw_candidates: list[tuple[Dict, Dict, float]],
    stats: dict,
    options: OverlapOptions,
) -> tuple[list[tuple[Dict, Dict]], dict]:
    candidates = raw_candidates
    if options.top_k_per_column > 0:
        candidates = _top_k_candidates_per_column(candidates, options.top_k_per_column)
        stats["top_k_skipped"] = len(raw_candidates) - len(candidates)

    candidates.sort(key=lambda item: (-item[2], item[0]["entity_name"], item[1]["entity_name"]))
    if options.max_value_candidate_pairs == 0 and candidates:
        stats["exceeded"] = True
        return [], stats
    if len(candidates) > options.max_value_candidate_pairs:
        stats["exceeded"] = True
        candidates = candidates[: options.max_value_candidate_pairs + 1]
    stats["candidate_pairs"] = len(candidates)
    return [(col1, col2) for col1, col2, _score in candidates], stats


def _top_k_candidates_per_column(
    candidates: list[tuple[Dict, Dict, float]],
    k: int,
) -> list[tuple[Dict, Dict, float]]:
    incident: dict[str, list[tuple[float, str, tuple[Dict, Dict, float]]]] = defaultdict(list)
    for item in candidates:
        col1, col2, score = item
        left_ref = col1["entity_name"]
        right_ref = col2["entity_name"]
        incident[left_ref].append((score, right_ref, item))
        incident[right_ref].append((score, left_ref, item))

    kept_edges: set[tuple[str, str]] = set()
    for col_ref, items in incident.items():
        selected = sorted(items, key=lambda item: (-item[0], item[1]))[:k]
        for _score, other_ref, _item in selected:
            kept_edges.add(tuple(sorted((col_ref, other_ref))))

    return [
        item for item in candidates
        if tuple(sorted((item[0]["entity_name"], item[1]["entity_name"]))) in kept_edges
    ]


def _build_column_domains(
    table_columns: dict[str, list[Dict]],
    table_group_memberships: dict[str, set[str]],
    options: OverlapOptions,
) -> tuple[list[Dict], dict]:
    columns = [col for cols in table_columns.values() for col in cols]
    physical_units = {_column_base_unit(col, table_group_memberships) for col in columns}
    unit_by_base = {unit: unit for unit in physical_units}
    pattern_clusters: dict[str, set[str]] = {unit: {unit} for unit in physical_units}

    if options.pattern_table_domain_enabled and len(physical_units) > 1:
        roles_by_unit: dict[str, set[str]] = defaultdict(set)
        schema_by_unit: dict[str, str] = {}
        for col in columns:
            unit = _column_base_unit(col, table_group_memberships)
            roles_by_unit[unit].add(_column_role_name(col.get("column", "")))
            schema_by_unit.setdefault(unit, str(col.get("schema_name") or ""))
        unit_by_base, pattern_clusters = _cluster_pattern_table_domains(
            roles_by_unit,
            schema_by_unit=schema_by_unit,
            threshold=options.pattern_table_domain_threshold,
        )

    grouped: dict[tuple[str, str], list[Dict]] = defaultdict(list)
    for col in columns:
        base_unit = _column_base_unit(col, table_group_memberships)
        unit = unit_by_base.get(base_unit, base_unit)
        role = _column_role_name(col.get("column", ""))
        grouped[(unit, role)].append(col)

    domains = [_make_column_domain(unit, role, members, pattern_clusters.get(unit, {unit})) for (unit, role), members in grouped.items()]
    domains.sort(key=lambda item: item["entity_name"])
    stats = {
        "physical_column_count": len(columns),
        "column_domain_count": len(domains),
        "multi_column_domain_count": sum(1 for domain in domains if len(domain.get("domain_members") or []) > 1),
        "pattern_table_domain_count": len(pattern_clusters),
        "multi_table_pattern_domain_count": sum(1 for members in pattern_clusters.values() if len(members) > 1),
    }
    return domains, stats


def _column_base_unit(col: Dict, memberships: dict[str, set[str]]) -> str:
    table_ref = str(col.get("table_ref") or col.get("table") or "")
    groups = sorted(memberships.get(table_ref, set()) or memberships.get(str(col.get("table")), set()))
    if groups:
        return "table_group:" + groups[0]
    return "table:" + table_ref


def _cluster_pattern_table_domains(
    roles_by_unit: dict[str, set[str]],
    *,
    schema_by_unit: dict[str, str],
    threshold: float,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    units = sorted(roles_by_unit)
    parent = {unit: unit for unit in units}

    def find(unit: str) -> str:
        while parent[unit] != unit:
            parent[unit] = parent[parent[unit]]
            unit = parent[unit]
        return unit

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        parent[max(left_root, right_root)] = min(left_root, right_root)

    for index, left in enumerate(units):
        for right in units[index + 1:]:
            if schema_by_unit.get(left, "") != schema_by_unit.get(right, ""):
                continue
            union_roles = roles_by_unit[left] | roles_by_unit[right]
            if len(union_roles) < MIN_PATTERN_TABLE_DOMAIN_ROLES:
                continue
            if _set_jaccard(roles_by_unit[left], roles_by_unit[right]) >= threshold:
                union(left, right)

    unit_by_base = {unit: "pattern_table:" + find(unit) for unit in units}
    clusters: dict[str, set[str]] = defaultdict(set)
    for unit, cluster in unit_by_base.items():
        clusters[cluster].add(unit)
    return unit_by_base, dict(clusters)


def _set_jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _column_role_name(column_name: str) -> str:
    text = str(column_name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = COLUMN_ROLE_SLOT_RE.sub("*", text)
    return text or "column"


def _make_column_domain(unit: str, role: str, members: list[Dict], unit_members: set[str]) -> Dict:
    sorted_members = sorted(members, key=lambda item: (item.get("table_name") or "", item.get("column") or "", item.get("entity_name") or ""))
    first = sorted_members[0]
    member_refs = [member["entity_name"] for member in sorted_members]
    digest = hashlib.sha1("|".join(member_refs).encode("utf-8")).hexdigest()[:12]
    unit_name = _logical_unit_display_name(unit)
    samples: list[Any] = []
    topk: list[Any] = []
    data_types: list[str] = []
    min_lengths: list[int] = []
    max_lengths: list[int] = []
    min_values: list[float] = []
    max_values: list[float] = []
    sample_seen: set[str] = set()
    topk_seen: set[str] = set()
    member_cardinality_sum = 0
    for member in sorted_members:
        data_type = str(member.get("data_type") or "")
        if data_type and data_type not in data_types:
            data_types.append(data_type)
        member_cardinality_sum += int(member.get("cardinality") or 0)
        if member.get("min_length") is not None:
            min_lengths.append(int(member["min_length"]))
        if member.get("max_length") is not None:
            max_lengths.append(int(member["max_length"]))
        if member.get("min_value") is not None:
            min_values.append(float(member["min_value"]))
        if member.get("max_value") is not None:
            max_values.append(float(member["max_value"]))
        for value in member.get("sample") or []:
            key = str(value).strip()
            if key and key not in sample_seen:
                sample_seen.add(key)
                samples.append(value)
        for item in member.get("topk") or []:
            value = item.get("value") if isinstance(item, dict) else item
            key = str(value).strip()
            if key and key not in topk_seen:
                topk_seen.add(key)
                topk.append(item)

    return {
        "entity_name": f"{unit}--column_domain--{role}--{digest}",
        "db_ref": first.get("db_ref"),
        "table": unit,
        "table_ref": unit,
        "table_name": unit_name,
        "schema_name": first.get("schema_name") or "",
        "column": role,
        "column_ref": f"{unit}--column_domain--{role}--{digest}",
        "data_type": data_types[0] if len(data_types) == 1 else "mixed",
        # A sum of member cardinalities double-counts values shared by shards,
        # so it is an upper bound rather than the logical column cardinality.
        # Exact distinct cardinality is calculated by the value matcher.
        "cardinality": 0,
        "member_cardinality_sum": member_cardinality_sum,
        "min_length": min(min_lengths) if min_lengths else None,
        "max_length": max(max_lengths) if max_lengths else None,
        "avg_length": None,
        "min_value": min(min_values) if min_values else None,
        "max_value": max(max_values) if max_values else None,
        "null_percentage": None,
        "sample": samples[:20],
        "topk": topk[:20],
        "domain_role": role,
        "domain_unit": unit,
        "domain_unit_members": sorted(unit_members),
        "domain_members": sorted_members,
        "domain_member_count": len(sorted_members),
        "domain_kind": "column_domain",
        "domain_profile": merge_domain_profiles(sorted_members),
    }


def _logical_unit_display_name(unit: str) -> str:
    value = str(unit or "")
    if ":" in value:
        value = value.split(":", 1)[1]
    return value.split("--")[-1] if "--" in value else value


def _should_keep_value_candidate(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    repeated_key_names: set[str],
) -> tuple[bool, str]:
    context = CandidateFilterContext(
        name_token_overlap=_name_token_overlap_candidate(col1, col2),
        repeated_key_names=repeated_key_names,
    )
    ok, reason = _apply_candidate_filter_stage(
        _structure_candidate_filters(options),
        col1,
        col2,
        options,
        context,
    )
    if not ok:
        return False, reason

    ok, reason = _apply_candidate_filter_stage(
        _name_candidate_filters(options),
        col1,
        col2,
        options,
        context,
    )
    if not ok:
        return False, reason

    # Value-domain checks are expensive. For Spider-style runs we can make
    # a shared name token the final cheap gate and defer evidence to MinHash/SQL.
    if options.name_token_overlap_first and context.name_token_overlap:
        return True, ""

    return _apply_candidate_filter_stage(
        _metadata_candidate_filters(options),
        col1,
        col2,
        options,
        context,
    )


CandidateFilter = Callable[[Dict, Dict, OverlapOptions, CandidateFilterContext], tuple[bool, str]]


def _apply_candidate_filter_stage(
    filters: Iterable[CandidateFilter],
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    for filter_fn in filters:
        ok, reason = filter_fn(col1, col2, options, context)
        if not ok:
            return False, reason
    return True, ""


def _structure_candidate_filters(options: OverlapOptions) -> list[CandidateFilter]:
    filters: list[CandidateFilter] = []
    if options.same_schema_only:
        filters.append(_filter_same_schema)
    return filters


def _name_candidate_filters(options: OverlapOptions) -> list[CandidateFilter]:
    filters: list[CandidateFilter] = []
    if options.require_name_token_overlap:
        filters.append(_filter_name_token_overlap)
    return filters


def _metadata_candidate_filters(options: OverlapOptions) -> list[CandidateFilter]:
    filters: list[CandidateFilter] = []
    if options.domain_filter_enabled:
        filters.append(_filter_domain_compatible)
    if options.key_like_only:
        filters.append(_filter_key_like)
    if options.require_repeated_key_name:
        filters.append(_filter_repeated_key_name)
    if options.shape_filter_enabled:
        filters.append(_filter_shape_compatible)
    return filters


def _filter_same_schema(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    return (True, "") if _same_schema(col1, col2) else (False, "same_schema_skipped")


def _filter_name_token_overlap(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    return (True, "") if context.name_token_overlap else (False, "name_token_skipped")


def _filter_domain_compatible(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    compatible, reason, _evidence = domain_compatibility(col1, col2)
    return (True, "") if compatible else (False, f"domain_{reason}")


def _filter_key_like(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    return (True, "") if _key_like_candidate(col1, col2) else (False, "key_like_skipped")


def _filter_repeated_key_name(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    if _has_repeated_key_name(col1, col2, context.repeated_key_names):
        return True, ""
    return False, "repeated_key_name_skipped"


def _filter_shape_compatible(
    col1: Dict,
    col2: Dict,
    options: OverlapOptions,
    context: CandidateFilterContext,
) -> tuple[bool, str]:
    return (True, "") if _shape_compatible(col1, col2) else (False, "shape_skipped")


def _same_schema(col1: Dict, col2: Dict) -> bool:
    schema1 = str(col1.get("schema_name") or "")
    schema2 = str(col2.get("schema_name") or "")
    return not schema1 or not schema2 or schema1 == schema2


def _repeated_key_names(column_groups: Iterable[list[Dict]]) -> set[str]:
    units_by_key: dict[str, set[str]] = defaultdict(set)
    for columns in column_groups:
        for col in columns:
            name_key = _normalized_key_name(col.get("column", ""))
            if not name_key:
                continue
            units_by_key[name_key].add(str(col.get("table") or ""))
    return {key for key, units in units_by_key.items() if len(units) >= 2}


def _has_repeated_key_name(col1: Dict, col2: Dict, repeated_key_names: set[str]) -> bool:
    name1 = _normalized_key_name(col1.get("column", ""))
    name2 = _normalized_key_name(col2.get("column", ""))
    return bool(name1 and name1 == name2 and name1 in repeated_key_names)


def _key_like_candidate(col1: Dict, col2: Dict) -> bool:
    tokens1 = _tokens(col1.get("column", ""))
    tokens2 = _tokens(col2.get("column", ""))
    name1 = _normalized_key_name(col1.get("column", ""))
    name2 = _normalized_key_name(col2.get("column", ""))
    if name1 and name1 == name2:
        return True
    shared_non_generic = (tokens1 - GENERIC_KEY_TOKENS) & (tokens2 - GENERIC_KEY_TOKENS)
    if shared_non_generic:
        return True
    if _has_compound_token_overlap(tokens1 - GENERIC_KEY_TOKENS, tokens2 - GENERIC_KEY_TOKENS):
        return True
    if tokens1 & tokens2 & KEYLIKE_TOKENS:
        return True
    family1 = _id_family(col1.get("column", ""))
    family2 = _id_family(col2.get("column", ""))
    if family1 and family1 == family2:
        return True
    if _generic_column_matches_other_table(col1, col2):
        return True
    if _generic_column_matches_other_table(col2, col1):
        return True
    if _is_generic_key_name(col1.get("column", "")) or _is_generic_key_name(col2.get("column", "")):
        return bool((tokens1 & tokens2) - GENERIC_KEY_TOKENS)
    return False


def _name_token_overlap_candidate(col1: Dict, col2: Dict) -> bool:
    return bool(_tokens(col1.get("column", "")) & _tokens(col2.get("column", "")))


def _candidate_score(col1: Dict, col2: Dict, options: OverlapOptions) -> float:
    tokens1 = _tokens(col1.get("column", ""))
    tokens2 = _tokens(col2.get("column", ""))
    score = 0.0
    name1 = _normalized_key_name(col1.get("column", ""))
    name2 = _normalized_key_name(col2.get("column", ""))
    if name1 and name1 == name2:
        score += 20.0
    shared = tokens1 & tokens2
    score += min(len(shared), 5) * 3.0
    if shared & KEYLIKE_TOKENS:
        score += 8.0
    family1 = _id_family(col1.get("column", ""))
    family2 = _id_family(col2.get("column", ""))
    if family1 and family1 == family2:
        score += 8.0
    if _length_ranges_equalish(col1, col2):
        score += 3.0
    if _cardinality_close(col1, col2):
        score += 2.0
    if _is_generic_key_name(col1.get("column", "")) or _is_generic_key_name(col2.get("column", "")):
        score -= float(options.generic_token_top_k)
    return score


def _shape_compatible(col1: Dict, col2: Dict) -> bool:
    """Return false only for a format contradiction proven by full-column stats.

    Samples and top-k values are useful evidence, but are not an admissible
    hard rejection rule: they can miss a rare but valid join value.  In
    contrast, non-overlapping full-column string-length ranges prove that two
    normalized strings cannot be equal.  This catches fixed 10-character vs
    fixed 9-character identifiers without turning sample heuristics into a
    recall-killing gate.
    """

    return _length_ranges_overlap(col1, col2)


def _shape_incompatible_reason(col1: Dict, col2: Dict) -> str:
    """Return the hard shape rejection reason used for audit output."""

    if not _length_ranges_overlap(col1, col2):
        return "length_ranges_disjoint"
    return ""


def _length_ranges_overlap(col1: Dict, col2: Dict) -> bool:
    min1, max1 = col1.get("min_length"), col1.get("max_length")
    min2, max2 = col2.get("min_length"), col2.get("max_length")
    if None in (min1, max1, min2, max2):
        return True
    return max(int(min1), int(min2)) <= min(int(max1), int(max2))


def _length_ranges_equalish(col1: Dict, col2: Dict) -> bool:
    min1, max1 = col1.get("min_length"), col1.get("max_length")
    min2, max2 = col2.get("min_length"), col2.get("max_length")
    if None in (min1, max1, min2, max2):
        return False
    return int(min1) == int(min2) and int(max1) == int(max2)


def _format_profile(values: list[Any]) -> dict[str, Any] | None:
    samples = [str(value).strip() for value in values if value not in (None, "")]
    if not samples:
        return None

    lengths = [len(value) for value in samples]
    shape_counts: dict[str, int] = defaultdict(int)
    for value in samples:
        shape_counts[_single_value_shape(value)] += 1

    shape, count = max(shape_counts.items(), key=lambda item: item[1])
    dominant_ratio = count / len(samples)
    if dominant_ratio < 0.8:
        shape = "mixed"

    length_counts: dict[int, int] = defaultdict(int)
    for length in lengths:
        length_counts[length] += 1
    length, length_count = max(length_counts.items(), key=lambda item: item[1])

    return {
        "class": shape,
        "class_confidence": round(dominant_ratio, 4),
        "sample_count": len(samples),
        "length": length,
        "length_confidence": round(length_count / len(lengths), 4),
        "fixed_length": length_count / len(lengths) >= 0.8,
    }


def _numeric_ranges_overlap(col1: Dict, col2: Dict) -> bool:
    min1, max1 = col1.get("min_value"), col1.get("max_value")
    min2, max2 = col2.get("min_value"), col2.get("max_value")
    if None in (min1, max1, min2, max2):
        return True
    return max(float(min1), float(min2)) <= min(float(max1), float(max2))


def _cardinality_close(col1: Dict, col2: Dict) -> bool:
    card1 = int(col1.get("cardinality") or 0)
    card2 = int(col2.get("cardinality") or 0)
    if card1 <= 0 or card2 <= 0:
        return False
    return max(card1, card2) / max(1, min(card1, card2)) <= 10


def _value_shape_class(col: Dict) -> str:
    profile = _column_shape_profile(col)
    return str((profile or {}).get("class") or "")


def _column_shape_profile(col: Dict) -> dict[str, Any] | None:
    """Describe observed format separately from full-column hard constraints."""

    profile = _format_profile(_shape_profile_values(col))
    if profile is None:
        profile = {}
    min_length = _optional_profile_int(col.get("min_length"))
    max_length = _optional_profile_int(col.get("max_length"))
    profile.update({
        "metadata_min_length": min_length,
        "metadata_max_length": max_length,
        "metadata_fixed_length": bool(
            min_length is not None and max_length is not None and min_length == max_length
        ),
    })
    return profile or None


def _profile_values(col: Dict) -> list[Any]:
    values: list[Any] = []
    sample = col.get("sample")
    if isinstance(sample, list):
        values.extend(sample[:20])
    topk = col.get("topk")
    if isinstance(topk, list):
        for item in topk[:20]:
            values.append(item.get("value") if isinstance(item, dict) else item)
    return values


def _shape_profile_values(col: Dict) -> list[Any]:
    """Prefer random samples; top-k is only a fallback because it is biased."""

    sample = col.get("sample")
    if isinstance(sample, list) and sample:
        return sample[:20]
    topk = col.get("topk")
    if not isinstance(topk, list):
        return []
    return [item.get("value") if isinstance(item, dict) else item for item in topk[:20]]


def _optional_profile_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _single_value_shape(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"[+-]?\d+", text):
        return "integer_text"
    if re.fullmatch(r"[+-]?\d+\.\d+", text):
        return "decimal_text"
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text):
        return "uuid"
    if re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        return "email"
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", text):
        return "ipv4"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "date_dash"
    if re.fullmatch(r"\d{8}", text):
        return "date_compact_or_8digit"
    if re.fullmatch(r"[0-9a-fA-F]+", text) and len(text) >= 8:
        return "hex"
    if re.fullmatch(r"[A-Za-z]+", text):
        return "alpha"
    if re.fullmatch(r"[A-Za-z0-9]+", text):
        return "alnum"
    if re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return "token"
    return "mixed"


def _shape_classes_compatible(shape1: str, shape2: str) -> bool:
    if shape1 == shape2:
        return True
    compatible_sets = [
        {"integer_text", "decimal_text"},
        {"integer_text", "date_compact_or_8digit", "alnum"},
        {"alpha", "alnum", "token"},
        {"mixed", "token", "alnum", "alpha"},
    ]
    return any(shape1 in shapes and shape2 in shapes for shapes in compatible_sets)


def _normalized_key_name(name: str) -> str:
    tokens = sorted(_tokens(name))
    return "_".join(tokens) if tokens else ""


def _is_generic_key_name(name: str) -> bool:
    tokens = _tokens(name)
    return bool(tokens) and tokens.issubset(GENERIC_KEY_TOKENS)


def _has_compound_token_overlap(tokens1: set[str], tokens2: set[str]) -> bool:
    for left in tokens1:
        for right in tokens2:
            if len(left) < 4 or len(right) < 4:
                continue
            if left in right or right in left:
                return True
    return False


def _generic_column_matches_other_table(generic_col: Dict, other_col: Dict) -> bool:
    column_tokens = _tokens(generic_col.get("column", ""))
    if not column_tokens or not column_tokens.issubset(GENERIC_KEY_TOKENS | {"symbol", "address"}):
        return False
    table_tokens = _table_name_tokens(generic_col)
    other_tokens = _tokens(other_col.get("column", "")) - GENERIC_KEY_TOKENS
    return bool(table_tokens and other_tokens and table_tokens & other_tokens)


def _table_name_tokens(col: Dict) -> set[str]:
    table_name = str(col.get("table_name") or col.get("table") or "")
    raw = table_name.split("--")[-1] if "--" in table_name else table_name
    tokens = {_singular_token(token) for token in _tokens(raw)}
    return {token for token in tokens if token and token not in GENERIC_KEY_TOKENS}


def _singular_token(token: str) -> str:
    text = str(token or "").lower()
    if text.endswith("ies") and len(text) > 4:
        return text[:-3] + "y"
    if text.endswith("s") and len(text) > 3:
        return text[:-1]
    return text


def _id_family(name: str) -> str:
    tokens = _tokens(name)
    families = {
        "patent": {"patent", "publication", "application", "family", "citation"},
        "person": {"user", "visitor", "customer", "person", "patient", "participant", "subject"},
        "clinical": {"case", "sample", "specimen", "aliquot", "barcode", "submitter"},
        "geo": {"geo", "country", "state", "county", "tract", "block", "zip", "postal"},
        "repo": {"repo", "repository", "file", "path", "commit"},
        "order": {"order", "product", "item", "customer"},
        "organization": {"assignee", "inventor"},
        "site": {"station", "site", "location"},
        "classification": {"cpc", "ipc", "class", "subclass", "group", "symbol"},
    }
    for family, family_tokens in families.items():
        if tokens & family_tokens:
            return family
    lowered = str(name or "").lower()
    if any(lowered.endswith(suffix) for suffix in KEYLIKE_SUFFIXES):
        return "id"
    return ""


def _table_group_representative_tables(
    table_refs: Iterable[str],
    memberships: dict[str, set[str]],
) -> dict[str, str]:
    representatives: dict[str, str] = {}
    for table_ref in sorted(str(ref) for ref in table_refs if ref):
        for group_ref in memberships.get(table_ref, set()):
            representatives.setdefault(group_ref, table_ref)
    return representatives


def _is_same_table_overlap_representative(
    table_ref: str,
    representative_tables: dict[str, str],
    memberships: dict[str, set[str]],
) -> bool:
    group_refs = memberships.get(str(table_ref), set())
    if not group_refs:
        return True
    return any(representative_tables.get(group_ref) == table_ref for group_ref in group_refs)


def _should_compare_value_domain(col1: Dict, col2: Dict) -> bool:
    """Compatibility wrapper retained for callers of the older helper."""

    compatible, _reason, _evidence = domain_compatibility(col1, col2)
    return compatible
