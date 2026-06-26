"""DB Column Overlap Generator - 数据库列候选重叠检测生成器

职责：
- 匹配 *.db 下的所有 *.*.*.col 节点
- 使用 Jaccard 相似度检测列值重叠
- 使用列名 token 检测列名重叠
- 合并同一列集合上的多种证据，并创建 labels=["overlap"] 的候选实体

检测流程：
1. Value Overlap Check - 值交集硬约束
2. Name Token Overlap Check - 列名 token 重叠
3. Evidence Merge - 合并同列集合证据

独立执行：
    python -m extractor.db_column_overlap ./my_data
"""
import logging
import re
import hashlib
from typing import List, Dict, Set, Optional
from collections import defaultdict
from itertools import combinations
from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, get_entity_meta, neo4j_props
from extractor.modules.utils.src import file_exists, open_sqlite_db

logger = logging.getLogger(__name__)

BOOLEAN_VALUES = {"0", "1", "true", "false", "t", "f", "yes", "no", "y", "n"}
NUMERIC_TYPES = {"int", "integer", "real", "float", "double", "decimal", "numeric"}
TEMPORAL_TYPES = {"date", "datetime", "timestamp", "time"}
MIN_OVERLAP_VALUES = 10
MIN_OVERLAP_COVERAGE_OVERRIDE = 0.8
SHORT_CODE_MAX_LENGTH = 4
SHORT_CODE_RATIO_THRESHOLD = 0.8
SHORT_CODE_MAX_COVERAGE = 0.5
MIN_GROUP_SIZE = 3
MAX_NAME_GROUP_COLUMNS = 12

STOP_TOKENS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or",
    "this", "that", "field", "column", "table",
}


def generate(workspace: Workspace, config=None) -> None:
    """为所有数据库检测列值重叠"""
    logger.info("=== Generating column overlaps ===")

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(
            "MATCH (n) WHERE n.name ENDS WITH $suffix RETURN n",
            params={"suffix": ext_suffix},
        )
        for db_row in db_rows:
            path = db_row["n"]["name"]
            try:
                _generate_for_database(path, workspace)
            except Exception as e:
                logger.warning(f"Failed to generate overlaps for {path}: {e}")


def _generate_for_database(path: str, workspace: Workspace) -> bool:
    """为单个数据库检测列值重叠"""
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": path})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", path) if db_meta else path
    if not file_exists(workspace, db_rel):
        return False

    # 收集所有列信息（通过 table → col 遍历）
    columns_info = []
    tbl_rows = workspace.cypher(
        "MATCH (d {name: $path})--(t:table) RETURN t",
        params={"path": path},
    )
    for tbl_row in tbl_rows:
        table_ref = tbl_row["t"]["name"]
        col_rows = workspace.cypher(
            "MATCH (d {name: $path})--(t {name: $table_ref})--(c:col) RETURN c",
            params={"path": path, "table_ref": table_ref},
        )
        for col_row in col_rows:
            col_name = col_row["c"]["name"]
            col_ref = db_column_ref(path, table_ref, col_name)
            col_meta = get_entity_meta(workspace, col_ref)
            if not col_meta:
                continue
            cardinality = col_meta.get("cardinality", 0)
            columns_info.append({
                'entity_name': col_ref,
                'table': table_ref,
                'column': col_name,
                'data_type': _column_type_from_labels(col_meta),
                'cardinality': cardinality,
            })
    if not columns_info:
        return False

    if len(columns_info) < 2:
        logger.info(f"  Skipping {path}: only {len(columns_info)} columns")
        return False

    _delete_existing_overlaps(path, workspace)
    value_sets = _load_column_value_sets(db_rel, columns_info, workspace)

    table_columns = defaultdict(list)
    for col in columns_info:
        table_columns[col['table']].append(col)

    # 检测所有列对；只要值域有重叠就进入候选图。
    pair_overlaps = []
    table_pairs = list(combinations(table_columns.keys(), 2))

    for table1, table2 in table_pairs:
        cols1 = table_columns[table1]
        cols2 = table_columns[table2]
        pair_overlaps.extend(_detect_column_overlaps(cols1, cols2, value_sets))

    # 同表列也可能共享值域并造成字段混用，例如 city/district。
    for cols in table_columns.values():
        for col1, col2 in combinations(cols, 2):
            pair_overlaps.extend(_detect_column_overlaps([col1], [col2], value_sets))

    value_overlaps = _group_pair_overlaps(pair_overlaps)
    name_overlaps = _detect_name_overlaps(columns_info)
    overlap_groups = _merge_overlap_evidence(value_overlaps + name_overlaps)
    created_count = 0
    for overlap in overlap_groups:
        if _create_overlap_entity(path, overlap, workspace):
            created_count += 1

    if created_count > 0:
        logger.info(f"  Overlaps: {path} ({created_count} relations)")
    return True


def _delete_existing_overlaps(path: str, workspace: Workspace) -> None:
    workspace.cypher(
        """
        MATCH (d {name: $path})--(t:table)--(o:overlap)
        DETACH DELETE o
        """,
        params={"path": path},
    )


def _load_column_value_sets(db_rel: str, columns_info: List[Dict], workspace: Workspace) -> Dict[str, Set]:
    """Load distinct non-null values for each column once per database."""
    value_sets: Dict[str, Set] = {}
    try:
        with open_sqlite_db(workspace, db_rel) as conn:
            cursor = conn.cursor()
            for col in columns_info:
                try:
                    cursor.execute(
                        f'SELECT DISTINCT "{col["column"]}" FROM "{col["table"]}" '
                        f'WHERE "{col["column"]}" IS NOT NULL'
                    )
                    value_sets[col["entity_name"]] = {row[0] for row in cursor.fetchall()}
                except Exception as exc:
                    logger.debug("Could not load values for %s: %s", col["entity_name"], exc)
                    value_sets[col["entity_name"]] = set()
    except Exception as exc:
        logger.debug("Could not load database values for overlap detection: %s", exc)
    return value_sets


def _detect_column_overlaps(cols1: List[Dict], cols2: List[Dict], value_sets: Dict[str, Set]) -> List[Dict]:
    """检测两表列之间的重叠"""
    overlaps = []

    for col1 in cols1:
        for col2 in cols2:
            overlap_result = _calculate_overlap(col1, col2, value_sets)
            if not overlap_result or overlap_result['card_overlap'] == 0:
                continue

            overlap_info = {
                'from_table': col1['table'],
                'from_column': col1['column'],
                'from_ref': col1['entity_name'],
                'from_type': col1['data_type'],
                'to_table': col2['table'],
                'to_column': col2['column'],
                'to_ref': col2['entity_name'],
                'to_type': col2['data_type'],
                'sources': ["value_domain"],
                'stats': {
                    'card_overlap': overlap_result['card_overlap'],
                    'jaccard': overlap_result['jaccard'],
                    'cardinality_A': col1['cardinality'],
                    'cardinality_B': col2['cardinality'],
                    'coverage_A_in_B': overlap_result['coverage_A_in_B'],
                    'coverage_B_in_A': overlap_result['coverage_B_in_A'],
                }
            }
            overlaps.append(overlap_info)

    def sort_key(x):
        return (-x['stats']['card_overlap'], -x['stats']['jaccard'])

    overlaps.sort(key=sort_key)
    return overlaps


def _detect_name_overlaps(columns_info: List[Dict]) -> List[Dict]:
    by_token: dict[str, list[Dict]] = defaultdict(list)
    for col in columns_info:
        for token in _tokens(col["column"]):
            by_token[token].append(col)

    overlaps: List[Dict] = []
    for token, cols in sorted(by_token.items()):
        unique = {col["entity_name"]: col for col in cols if col["entity_name"]}
        selected = sorted(unique.values(), key=lambda col: (col["table"], col["column"], col["entity_name"]))
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


def _column_payload(col: Dict) -> Dict:
    return {
        "ref": col["entity_name"],
        "table": col["table"],
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
    """Build mutually exclusive value-domain groups using jaccard as structure signal."""
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
            "column": overlap["from_column"],
            "type": overlap["from_type"],
        }
        columns[right] = {
            "ref": right,
            "table": overlap["to_table"],
            "column": overlap["to_column"],
            "type": overlap["to_type"],
        }
        by_edge[frozenset((left, right))] = overlap

    grouped_edges: set[frozenset[str]] = set()
    groups: list[Dict] = []

    for component in _connected_components(set(columns), by_edge):
        if len(component) < MIN_GROUP_SIZE:
            continue

        partition, _selected_edges = _jaccard_partition_component(component, by_edge)
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


def _jaccard_partition_component(
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
        best_edge, _overlap = min(edges, key=lambda item: _jaccard_edge_sort_key(item[0], item[1]))
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


def _jaccard_edge_sort_key(edge: frozenset[str], overlap: Dict):
    return (
        -overlap["stats"]["jaccard"],
        -max(overlap["stats"]["coverage_A_in_B"], overlap["stats"]["coverage_B_in_A"]),
        -overlap["stats"]["card_overlap"],
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
        })

    stats_values = [by_edge[edge_key]["stats"] for edge_key in group_edges]
    return {
        "columns": [columns[ref] for ref in sorted(refs)],
        "sources": ["value_domain"],
        "pair_stats": pair_stats,
        "stats": {
            "column_count": len(refs),
            "pair_count": len(pair_stats),
            "min_card_overlap": min(stat["card_overlap"] for stat in stats_values),
            "max_card_overlap": max(stat["card_overlap"] for stat in stats_values),
            "min_jaccard": min(stat["jaccard"] for stat in stats_values),
            "max_jaccard": max(stat["jaccard"] for stat in stats_values),
        },
    }


def _overlap_sort_key(overlap: Dict):
    if "columns" in overlap:
        return (
            0,
            -overlap["stats"].get("column_count", 0),
            -overlap["stats"].get("max_jaccard", 0),
            -overlap["stats"].get("name_token_count", 0),
            _group_overlap_name(overlap["columns"]),
        )
    return (
        1,
        -overlap["stats"].get("jaccard", 0),
        -overlap["stats"].get("card_overlap", 0),
        overlap["from_ref"],
        overlap["to_ref"],
    )


def _calculate_overlap(col1: Dict, col2: Dict, value_sets: Dict[str, Set]) -> Optional[Dict]:
    """计算两列的值重叠情况"""
    try:
        values1 = value_sets.get(col1["entity_name"], set())
        values2 = value_sets.get(col2["entity_name"], set())

        if values1.isdisjoint(values2):
            return None

        intersection = values1 & values2
        union = values1 | values2

        card_overlap = len(intersection)
        card_1 = len(values1)
        card_2 = len(values2)

        if _is_disabled_overlap(col1, col2, values1, values2, intersection):
            return None

        jaccard = card_overlap / len(union) if union else 0.0
        coverage_1_in_2 = card_overlap / card_1 if card_1 > 0 else 0.0
        coverage_2_in_1 = card_overlap / card_2 if card_2 > 0 else 0.0

        return {
            'card_overlap': card_overlap,
            'jaccard': round(jaccard, 4),
            'coverage_A_in_B': round(coverage_1_in_2, 4),
            'coverage_B_in_A': round(coverage_2_in_1, 4),
        }

    except Exception as e:
        logger.debug(f"Could not calculate overlap: {e}")
        return None


def _is_disabled_overlap(col1: Dict, col2: Dict, values1: Set, values2: Set, intersection: Set) -> bool:
    """Hard-filter value-domain overlaps that are almost always structural noise.

    These rules intentionally do not inspect column names. They only use value
    sets and declared SQLite types.
    """
    if _below_overlap_threshold(values1, values2, intersection):
        return True
    if len(values1) <= 1 or len(values2) <= 1:
        return True
    if _is_boolean_domain(values1) and _is_boolean_domain(values2):
        return True
    if _is_short_code_collision(values1, values2, intersection):
        return True
    if _is_numeric_type(col1.get("data_type")) and _is_numeric_type(col2.get("data_type")):
        return True
    if _is_temporal_type(col1.get("data_type")) and _is_temporal_type(col2.get("data_type")):
        return True
    return False


def _is_boolean_domain(values: Set) -> bool:
    normalized = {_normalize_value(value) for value in values}
    normalized.discard("")
    return 0 < len(normalized) <= 2 and normalized.issubset(BOOLEAN_VALUES)


def _below_overlap_threshold(values1: Set, values2: Set, intersection: Set) -> bool:
    if len(intersection) >= MIN_OVERLAP_VALUES:
        return False
    coverage_1 = len(intersection) / len(values1) if values1 else 0.0
    coverage_2 = len(intersection) / len(values2) if values2 else 0.0
    return max(coverage_1, coverage_2) < MIN_OVERLAP_COVERAGE_OVERRIDE


def _is_short_code_collision(values1: Set, values2: Set, intersection: Set) -> bool:
    normalized = [_normalize_value(value) for value in intersection]
    normalized = [value for value in normalized if value]
    if not normalized:
        return True

    short_code_count = sum(1 for value in normalized if _is_short_code_value(value))
    short_code_ratio = short_code_count / len(normalized)
    if short_code_ratio < SHORT_CODE_RATIO_THRESHOLD:
        return False

    coverage_1 = len(intersection) / len(values1) if values1 else 0.0
    coverage_2 = len(intersection) / len(values2) if values2 else 0.0
    return max(coverage_1, coverage_2) < SHORT_CODE_MAX_COVERAGE


def _is_short_code_value(value: str) -> bool:
    return (
        len(value) <= SHORT_CODE_MAX_LENGTH
        and bool(re.fullmatch(r"[a-z0-9]+", value))
        and any(char.isdigit() for char in value)
    )


def _is_numeric_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(token in lowered for token in NUMERIC_TYPES)


def _is_temporal_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(token in lowered for token in TEMPORAL_TYPES)


def _normalize_value(value) -> str:
    return str(value).strip().lower()


def _create_overlap_entity(path: str, overlap: Dict, workspace: Workspace) -> bool:
    """在 _entity/ 下为重叠关系创建实体（labels=["overlap"]）"""
    try:
        if "columns" in overlap:
            return _create_group_overlap_entity(path, overlap, workspace)

        from_table = overlap['from_table']
        from_column = overlap['from_column']
        to_table = overlap['to_table']
        to_column = overlap['to_column']

        raw_from_table = from_table.split("--")[-1] if "--" in from_table else from_table
        raw_to_table = to_table.split("--")[-1] if "--" in to_table else to_table
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

        from_col_ref = db_column_ref(path, from_table, from_column)
        to_col_ref = db_column_ref(path, to_table, to_column)

        if existing_name:
            _connect_overlap_edges(workspace, existing_name, from_table, to_table, from_col_ref, to_col_ref)
            return False

        workspace.cypher(
            "CREATE (o:overlap {name: $name}) SET o += $props",
            params={
                "name": overlapname,
                "props": neo4j_props({
                    "labels": ["overlap"],
                    "table_scope": _table_scope([from_table, to_table]),
                    "sources": overlap.get("sources", ["value_domain"]),
                    "stats": overlap["stats"],
                    "created_at": __import__("datetime").datetime.now().isoformat(),
                }),
            },
        )

        _connect_overlap_edges(workspace, overlapname, from_table, to_table, from_col_ref, to_col_ref)

        return True

    except Exception as e:
        logger.debug(f"Could not create overlap file: {e}")
        return False


def _create_group_overlap_entity(path: str, overlap: Dict, workspace: Workspace) -> bool:
    """Create one overlap entity for a fully-connected value-domain column group."""
    try:
        columns = overlap["columns"]
        overlapname = _group_overlap_name(columns)

        existing = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": overlapname})
        if existing:
            _connect_group_overlap_edges(workspace, overlapname, columns)
            return False

        workspace.cypher(
            "CREATE (o:overlap {name: $name}) SET o += $props",
            params={
                "name": overlapname,
                "props": neo4j_props({
                    "labels": ["overlap"],
                    "table_scope": _table_scope([column["table"] for column in columns]),
                    "sources": overlap.get("sources", ["value_domain"]),
                    "pair_stats": overlap.get("pair_stats", []),
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


def _group_overlap_name(columns: list[Dict]) -> str:
    labels = [_column_display_name(col) for col in columns]
    digest = hashlib.sha1("|".join(sorted(col["ref"] for col in columns)).encode("utf-8")).hexdigest()[:10]
    readable = "__".join(label.replace("/", "_").replace("\\", "_") for label in labels[:3])
    suffix = "" if len(labels) <= 3 else f"__plus{len(labels) - 3}"
    return f"value_domain[{readable}{suffix}]#{digest}"


def _table_scope(tables: list[str]) -> str:
    return "intra_table" if len(set(tables)) == 1 else "inter_table"


def _column_display_name(column: Dict) -> str:
    raw_table = column["table"].split("--")[-1] if "--" in column["table"] else column["table"]
    raw_column = column["column"].split("--")[-1] if "--" in column["column"] else column["column"]
    return f"{raw_table}.{raw_column}"


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
    workspace.cypher(
        """
        MATCH (o {name: $overlap_name})
        MATCH (a)
        WHERE a.name = $entity_ref
           OR a._ref = $entity_ref
           OR a.ref = $entity_ref
           OR a.path = $entity_ref
        MERGE (a)-[:RELATED_TO]->(o)
        """,
        params={"entity_ref": entity_ref, "overlap_name": overlap_name},
    )
