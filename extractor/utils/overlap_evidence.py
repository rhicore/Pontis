"""Evidence grouping and overlap-entity payload helpers."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Dict, List

from extractor.utils.overlap_candidates import _tokens
from extractor.utils.overlap_options import MAX_NAME_GROUP_COLUMNS, MIN_GROUP_SIZE

def _detect_name_overlaps(
    columns_info: List[Dict],
    table_group_memberships: dict[str, set[str]] | None = None,
) -> List[Dict]:
    by_token: dict[str, list[Dict]] = defaultdict(list)
    for col in columns_info:
        for token in _tokens(col["column"]):
            by_token[token].append(col)

    overlaps: List[Dict] = []
    for token, cols in sorted(by_token.items()):
        unique = {col["entity_name"]: col for col in cols if col["entity_name"]}
        selected = sorted(unique.values(), key=lambda col: (col["table"], col["column"], col["entity_name"]))
        selected = _collapse_columns_for_same_table_group(selected, table_group_memberships or {})
        if len(selected) < 2:
            continue
        selected = selected[:MAX_NAME_GROUP_COLUMNS]
        overlaps.append({
            "columns": [_column_payload(col) for col in selected],
            "sources": ["name_keyword"],
            "stats": {
                "column_count": len(selected),
                "name_token_count": 1,
                "name_tokens": [token],
            },
        })
    return overlaps


def _collapse_columns_for_same_table_group(
    columns: list[Dict],
    memberships: dict[str, set[str]],
) -> list[Dict]:
    if not memberships:
        return columns
    selected: list[Dict] = []
    seen_group_refs: set[str] = set()
    for col in sorted(columns, key=lambda item: (item["table"], item["column"], item.get("entity_name") or item.get("ref") or "")):
        group_refs = (
            memberships.get(str(col.get("table")), set())
            or memberships.get(str(col.get("table_ref")), set())
        )
        if group_refs:
            group_key = sorted(group_refs)[0]
            if group_key in seen_group_refs:
                continue
            seen_group_refs.add(group_key)
        selected.append(col)
    return selected


def _collapse_same_table_group_columns(
    overlap: Dict,
    memberships: dict[str, set[str]],
) -> Optional[Dict]:
    if not memberships or "columns" not in overlap:
        return overlap

    kept_columns = _collapse_columns_for_same_table_group(list(overlap.get("columns") or []), memberships)
    if len(kept_columns) < 2:
        return None

    kept_refs = {col["ref"] for col in kept_columns}
    collapsed = dict(overlap)
    collapsed["columns"] = kept_columns
    if "pair_stats" in collapsed:
        pair_stats = [
            item for item in collapsed.get("pair_stats") or []
            if item.get("from_ref") in kept_refs and item.get("to_ref") in kept_refs
        ]
        if not pair_stats:
            return None
        collapsed["pair_stats"] = pair_stats
        collapsed["stats"] = _recompute_group_stats(collapsed.get("stats") or {}, pair_stats, len(kept_columns))
    elif isinstance(collapsed.get("stats"), dict):
        stats = dict(collapsed["stats"])
        stats["column_count"] = len(kept_columns)
        collapsed["stats"] = stats
    return collapsed


def _recompute_group_stats(stats: dict, pair_stats: list[dict], column_count: int) -> dict:
    updated = dict(stats)
    updated["column_count"] = column_count
    updated["pair_count"] = len(pair_stats)
    stat_values = [item.get("stats") or {} for item in pair_stats]
    if stat_values:
        for target, source in (
            ("min_overlap_coefficient", "overlap_coefficient"),
            ("max_overlap_coefficient", "overlap_coefficient"),
        ):
            values = [value.get(source) for value in stat_values if value.get(source) is not None]
            if not values:
                continue
            updated[target] = min(values) if target.startswith("min_") else max(values)
    return updated


def _column_payload(col: Dict) -> Dict:
    return {
        "ref": col["entity_name"],
        "table": col["table"],
        "table_name": col.get("table_name") or col["table"],
        "table_ref": col.get("table_ref") or col["table"],
        "column": col["column"],
        "type": col.get("data_type", ""),
    }


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


def _merge_overlap_evidence(overlaps: List[Dict]) -> List[Dict]:
    merged: dict[tuple[str, ...], Dict] = {}
    for overlap in overlaps:
        key = _overlap_column_key(overlap)
        if len(key) < 2:
            continue
        if key not in merged:
            overlap["sources"] = _sources(overlap)
            merged[key] = overlap
            continue
        merged[key] = _merge_two_overlaps(merged[key], overlap)

    result = list(merged.values())
    result.sort(key=_overlap_sort_key)
    return result


def _overlap_column_key(overlap: Dict) -> tuple[str, ...]:
    if "columns" in overlap:
        return tuple(sorted(col["ref"] for col in overlap["columns"]))
    return tuple(sorted((overlap["from_ref"], overlap["to_ref"])))


def _sources(overlap: Dict) -> list[str]:
    if overlap.get("sources"):
        return sorted(set(overlap["sources"]))
    return ["value_domain"]


def _merge_two_overlaps(left: Dict, right: Dict) -> Dict:
    left["sources"] = sorted(set(_sources(left)) | set(_sources(right)))
    if "pair_stats" in right:
        merged_pair_stats = list(left.get("pair_stats") or [])
        merged_pair_stats.extend(right.get("pair_stats") or [])
        left["pair_stats"] = merged_pair_stats
    if "columns" not in left and "columns" in right:
        left["columns"] = right["columns"]

    stats = dict(left.get("stats") or {})
    right_stats = right.get("stats") or {}
    name_tokens = sorted(set(stats.get("name_tokens") or []) | set(right_stats.get("name_tokens") or []))
    if name_tokens:
        stats["name_tokens"] = name_tokens
        stats["name_token_count"] = len(name_tokens)
    left["stats"] = stats
    return left


def _group_pair_overlaps(pair_overlaps: List[Dict]) -> List[Dict]:
    """Build mutually exclusive value-domain groups using overlap coefficient."""
    if not pair_overlaps:
        return []

    columns: dict[str, Dict] = {}
    by_edge: dict[frozenset[str], Dict] = {}

    for overlap in pair_overlaps:
        left = overlap["from_ref"]
        right = overlap["to_ref"]
        columns[left] = {
            "ref": left,
            "table": overlap["from_table"],
            "table_name": overlap.get("from_table_name") or overlap["from_table"],
            "table_ref": overlap["from_table"],
            "column": overlap["from_column"],
            "type": overlap["from_type"],
        }
        columns[right] = {
            "ref": right,
            "table": overlap["to_table"],
            "table_name": overlap.get("to_table_name") or overlap["to_table"],
            "table_ref": overlap["to_table"],
            "column": overlap["to_column"],
            "type": overlap["to_type"],
        }
        by_edge[frozenset((left, right))] = overlap

    grouped_edges: set[frozenset[str]] = set()
    groups: list[Dict] = []

    for component in _connected_components(set(columns), by_edge):
        if len(component) < MIN_GROUP_SIZE:
            continue

        partition, _selected_edges = _overlap_coefficient_partition_component(component, by_edge)
        for refs in partition:
            if len(refs) < MIN_GROUP_SIZE:
                continue
            group_edges = {
                edge for edge in by_edge
                if edge.issubset(refs)
            }
            if not group_edges:
                continue
            groups.append(_make_group_overlap(tuple(sorted(refs)), columns, by_edge, group_edges))
            grouped_edges.update(group_edges)

    for overlap in pair_overlaps:
        edge = frozenset((overlap["from_ref"], overlap["to_ref"]))
        if edge in grouped_edges:
            continue
        groups.append(overlap)

    groups.sort(key=_overlap_sort_key)

    return groups


def _connected_components(nodes: set[str], by_edge: dict[frozenset[str], Dict]) -> list[set[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in by_edge:
        left, right = tuple(edge)
        neighbors[left].add(right)
        neighbors[right].add(left)

    seen: set[str] = set()
    components: list[set[str]] = []
    for node in sorted(nodes):
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        component = set()
        while stack:
            current = stack.pop()
            component.add(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        components.append(component)
    return components


def _overlap_coefficient_partition_component(
    component: set[str],
    by_edge: dict[frozenset[str], Dict],
) -> tuple[list[set[str]], set[frozenset[str]]]:
    selected_edges: set[frozenset[str]] = set()
    incident: dict[str, list[tuple[frozenset[str], Dict]]] = defaultdict(list)
    for edge, overlap in by_edge.items():
        if not edge.issubset(component):
            continue
        left, right = tuple(edge)
        incident[left].append((edge, overlap))
        incident[right].append((edge, overlap))

    for node, edges in incident.items():
        best_edge, _overlap = min(edges, key=lambda item: _overlap_coefficient_edge_sort_key(item[0], item[1]))
        selected_edges.add(best_edge)

    selected_neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in selected_edges:
        left, right = tuple(edge)
        selected_neighbors[left].add(right)
        selected_neighbors[right].add(left)

    seen: set[str] = set()
    groups: list[set[str]] = []
    for node in sorted(component):
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        group = set()
        while stack:
            current = stack.pop()
            group.add(current)
            for neighbor in sorted(selected_neighbors[current]):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        groups.append(group)

    return groups, selected_edges


def _overlap_coefficient_edge_sort_key(edge: frozenset[str], overlap: Dict):
    stats = overlap["stats"]
    if "jaccard" in stats:
        return (
            -stats["jaccard"],
            -max(stats.get("coverage_A_in_B", 0), stats.get("coverage_B_in_A", 0)),
            -stats.get("card_overlap", 0),
            sorted(edge),
        )
    return (
        -stats["overlap_coefficient"],
        sorted(edge),
    )


def _make_group_overlap(
    refs: tuple[str, ...],
    columns: dict[str, Dict],
    by_edge: dict[frozenset[str], Dict],
    group_edges: set[frozenset[str]],
) -> Dict:
    pair_stats = []
    for edge_key in sorted(group_edges, key=lambda edge: sorted(edge)):
        left, right = sorted(edge_key)
        edge = by_edge[edge_key]
        pair_stats.append({
            "from_ref": left,
            "to_ref": right,
            "stats": edge["stats"],
            "filter_evidence": edge.get("filter_evidence", {}),
        })

    stats_values = [by_edge[edge_key]["stats"] for edge_key in group_edges]
    return {
        "columns": [columns[ref] for ref in sorted(refs)],
        "sources": ["value_domain"],
        "pair_stats": pair_stats,
        "stats": {
            "column_count": len(refs),
            "pair_count": len(pair_stats),
            "min_overlap_coefficient": min(stat["overlap_coefficient"] for stat in stats_values),
            "max_overlap_coefficient": max(stat["overlap_coefficient"] for stat in stats_values),
        },
    }


def _overlap_sort_key(overlap: Dict):
    if "columns" in overlap:
        return (
            0,
            -overlap["stats"].get("column_count", 0),
            -overlap["stats"].get("max_overlap_coefficient", 0),
            -overlap["stats"].get("name_token_count", 0),
            _group_overlap_name(overlap["columns"]),
        )
    return (
        1,
        -overlap["stats"].get("overlap_coefficient", 0),
        overlap["from_ref"],
        overlap["to_ref"],
    )


def _group_overlap_name(columns: list[Dict]) -> str:
    labels = [_column_display_name(col) for col in columns]
    digest = hashlib.sha1("|".join(sorted(col["ref"] for col in columns)).encode("utf-8")).hexdigest()[:10]
    readable = "__".join(label.replace("/", "_").replace("\\", "_") for label in labels[:3])
    suffix = "" if len(labels) <= 3 else f"__plus{len(labels) - 3}"
    return f"value_domain[{readable}{suffix}]#{digest}"


def _table_scope(tables: list[str]) -> str:
    return "intra_table" if len(set(tables)) == 1 else "inter_table"


def _column_display_name(column: Dict) -> str:
    table_name = column.get("table_name") or column["table"]
    raw_table = table_name.split("--")[-1] if "--" in table_name else table_name
    raw_column = column["column"].split("--")[-1] if "--" in column["column"] else column["column"]
    return f"{raw_table}.{raw_column}"
