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
        overlap = {
            "columns": [_column_payload(col) for col in selected],
            "sources": ["name_keyword"],
            "stats": {
                "column_count": len(selected),
                "name_token_count": 1,
                "name_tokens": [token],
            },
        }
        domain_sides = [side for side in (_column_domain_side(col) for col in selected) if side]
        if domain_sides:
            overlap["domain_sides"] = domain_sides
        overlaps.append(overlap)
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
        # A logical column domain has already absorbed its physical table-group
        # members.  It is now a first-class column and must not be collapsed a
        # second time using physical table membership.
        if col.get("domain_members"):
            selected.append(col)
            continue
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


def _column_domain_side(col: Dict) -> Dict | None:
    members = col.get("domain_members")
    if not isinstance(members, list) or not members:
        return None
    return {
        "domain_ref": col.get("entity_name"),
        "domain_unit": col.get("domain_unit") or col.get("table"),
        "domain_role": col.get("domain_role") or col.get("column"),
        "domain_member_count": len(members),
        "members": [_column_payload(member) for member in members],
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
        columns[left] = _overlap_endpoint_column(overlap, "from")
        columns[right] = _overlap_endpoint_column(overlap, "to")
        by_edge[frozenset((left, right))] = overlap

    grouped_edges: set[frozenset[str]] = set()
    groups: list[Dict] = []
    edges_by_node: dict[str, set[frozenset[str]]] = defaultdict(set)
    for edge in by_edge:
        for ref in edge:
            edges_by_node[ref].add(edge)

    for component in _connected_components(set(columns), by_edge):
        if len(component) < MIN_GROUP_SIZE:
            continue

        component_edges: set[frozenset[str]] = set()
        for ref in component:
            component_edges.update(edges_by_node.get(ref, set()))

        partition, _selected_edges = _overlap_coefficient_partition_component(
            component,
            by_edge,
            component_edges,
        )
        for refs in partition:
            if len(refs) < MIN_GROUP_SIZE:
                continue
            group_edges = {edge for edge in component_edges if edge.issubset(refs)}
            if not group_edges:
                continue
            groups.append(_make_group_overlap(tuple(sorted(refs)), columns, by_edge, group_edges))
            grouped_edges.update(group_edges)

    for overlap in pair_overlaps:
        edge = frozenset((overlap["from_ref"], overlap["to_ref"]))
        if edge in grouped_edges:
            continue
        if overlap.get("domain_sides"):
            groups.append(_make_group_overlap(
                tuple(sorted((overlap["from_ref"], overlap["to_ref"]))),
                columns,
                by_edge,
                {edge},
            ))
        else:
            groups.append(overlap)

    groups.sort(key=_overlap_sort_key)

    return groups


def _overlap_endpoint_column(overlap: Dict, prefix: str) -> Dict:
    ref = str(overlap[f"{prefix}_ref"])
    side_index = 0 if prefix == "from" else 1
    domain_sides = overlap.get("domain_sides") or []
    domain_side = next(
        (side for side in domain_sides if str(side.get("domain_ref") or "") == ref),
        domain_sides[side_index] if len(domain_sides) > side_index else None,
    )
    if domain_side:
        domain_unit = str(domain_side.get("domain_unit") or overlap[f"{prefix}_table"])
        domain_role = str(domain_side.get("domain_role") or overlap[f"{prefix}_column"])
        return {
            "ref": ref,
            "table": domain_unit,
            "table_name": domain_unit,
            "table_ref": domain_unit,
            "column": domain_role,
            "type": overlap[f"{prefix}_type"],
            "_domain_side": domain_side,
        }
    table = overlap[f"{prefix}_table"]
    return {
        "ref": ref,
        "table": table,
        "table_name": overlap.get(f"{prefix}_table_name") or table,
        "table_ref": table,
        "column": overlap[f"{prefix}_column"],
        "type": overlap[f"{prefix}_type"],
    }


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
    component_edges: set[frozenset[str]] | None = None,
) -> tuple[list[set[str]], set[frozenset[str]]]:
    selected_edges: set[frozenset[str]] = set()
    incident: dict[str, list[tuple[frozenset[str], Dict]]] = defaultdict(list)
    for edge in component_edges or set(by_edge):
        overlap = by_edge[edge]
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
    selected_columns = [columns[ref] for ref in sorted(refs)]
    domain_sides = {
        str(column["_domain_side"].get("domain_ref") or column["ref"]): column["_domain_side"]
        for column in selected_columns
        if column.get("_domain_side")
    }
    public_columns = [
        {key: value for key, value in column.items() if key != "_domain_side"}
        for column in selected_columns
    ]
    payload = {
        "columns": public_columns,
        "sources": ["value_domain"],
        "pair_stats": pair_stats,
        "stats": {
            "column_count": len(refs),
            "pair_count": len(pair_stats),
            "min_overlap_coefficient": min(stat["overlap_coefficient"] for stat in stats_values),
            "max_overlap_coefficient": max(stat["overlap_coefficient"] for stat in stats_values),
        },
    }
    if domain_sides:
        payload["domain_sides"] = [domain_sides[ref] for ref in sorted(domain_sides)]
    return payload


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
    digest = hashlib.sha1("|".join(sorted(col["ref"] for col in columns)).encode("utf-8")).hexdigest()[:10]
    return f"value_domain_{digest}"


def _table_scope(tables: list[str]) -> str:
    return "intra_table" if len(set(tables)) == 1 else "inter_table"
