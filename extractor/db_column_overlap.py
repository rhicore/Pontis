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
import os
import re
import hashlib
from typing import Iterable, List, Dict, Optional
from collections import defaultdict
from itertools import combinations
from storage.workspace import Workspace
from extractor.utils.refs import neo4j_props

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
DEFAULT_MAX_VALUE_CANDIDATE_PAIRS = 5000
INTERSECTION_SAMPLE_LIMIT = 25
TABLE_COLUMN_BATCH_SIZE = 32

STOP_TOKENS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or",
    "this", "that", "field", "column", "table",
}


def generate(workspace: Workspace, config=None) -> None:
    """为所有 storage-backed database projects 检测列值重叠."""
    logger.info("=== Generating column overlaps ===")

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
            _generate_for_database(db_ref, db_node, db_connect, workspace)
        except Exception as e:
            logger.warning(f"Failed to generate overlaps for {db_ref}: {e}")


def _generate_for_database(db_ref: str, db_node: dict, db_connect, workspace: Workspace) -> bool:
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

    max_value_pairs = _resolve_max_value_candidate_pairs()
    value_candidates, candidate_stats = _collect_value_candidate_pairs(
        table_columns,
        table_group_memberships,
        max_pairs=max_value_pairs,
    )
    pair_overlaps: list[Dict] = []
    if candidate_stats["exceeded"]:
        logger.warning(
            "  Skipping value-domain overlaps for %s: candidate pairs exceed cap %s "
            "(set PONTIS_OVERLAP_MAX_VALUE_PAIRS to override)",
            db_ref,
            max_value_pairs,
        )
    elif value_candidates:
        pair_overlaps = _detect_column_overlaps_sql(db_connect, dialect, value_candidates)
    logger.info(
        "  Value-overlap candidates for %s: %s%s",
        db_ref,
        candidate_stats["candidate_pairs"],
        " (skipped by cap)" if candidate_stats["exceeded"] else "",
    )

    value_overlaps = [
        overlap
        for overlap in (
            _collapse_same_table_group_columns(overlap, table_group_memberships)
            for overlap in _group_pair_overlaps(pair_overlaps)
        )
        if overlap is not None
    ]
    name_overlaps = _detect_name_overlaps(columns_info, table_group_memberships)
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
                "table": table_ref,
                "table_ref": table_ref,
                "table_name": table_name,
                "schema_name": schema_name,
                "column": column_name,
                "column_ref": col_ref,
                "data_type": str(col.get("data_type") or _column_type_from_labels(col)),
                "cardinality": int(col.get("cardinality") or 0),
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


def _qualified_table_sql(col: Dict, dialect: str) -> str:
    table = _quote_identifier(col["table_name"], dialect)
    schema_name = str(col.get("schema_name") or "").strip()
    if schema_name and dialect not in {"sqlite", "duckdb"}:
        return f"{_quote_identifier(schema_name, dialect)}.{table}"
    return table


def _quote_identifier(name: str, dialect: str = "") -> str:
    text = str(name or "")
    return '"' + text.replace('"', '""') + '"'


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


def _same_table_group(table1: str, table2: str, memberships: dict[str, set[str]]) -> bool:
    groups1 = memberships.get(str(table1), set())
    groups2 = memberships.get(str(table2), set())
    return bool(groups1 and groups2 and groups1 & groups2)


def _resolve_max_value_candidate_pairs() -> int:
    raw = os.environ.get("PONTIS_OVERLAP_MAX_VALUE_PAIRS")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("Invalid PONTIS_OVERLAP_MAX_VALUE_PAIRS=%r, using default", raw)
    return DEFAULT_MAX_VALUE_CANDIDATE_PAIRS


def _collect_value_candidate_pairs(
    table_columns: dict[str, list[Dict]],
    table_group_memberships: dict[str, set[str]],
    *,
    max_pairs: int,
) -> tuple[list[tuple[Dict, Dict]], dict]:
    """Collect KG-filtered value-domain candidates before touching the database."""

    candidates: list[tuple[Dict, Dict]] = []
    stats = {
        "candidate_pairs": 0,
        "same_group_skipped": 0,
        "same_table_group_duplicate_skipped": 0,
        "type_skipped": 0,
        "exceeded": False,
    }

    def add_pair(col1: Dict, col2: Dict) -> bool:
        if not _should_compare_value_domain(col1, col2):
            stats["type_skipped"] += 1
            return True
        if max_pairs == 0:
            stats["exceeded"] = True
            return False
        candidates.append((col1, col2))
        stats["candidate_pairs"] += 1
        if len(candidates) > max_pairs:
            stats["exceeded"] = True
            return False
        return True

    table_refs = sorted(table_columns)
    for index, table1 in enumerate(table_refs):
        cols1 = table_columns[table1]
        for table2 in table_refs[index + 1:]:
            if _same_table_group(table1, table2, table_group_memberships):
                stats["same_group_skipped"] += len(cols1) * len(table_columns[table2])
                continue
            for col1 in cols1:
                for col2 in table_columns[table2]:
                    if not add_pair(col1, col2):
                        return candidates, stats

    representative_tables = _table_group_representative_tables(table_columns.keys(), table_group_memberships)
    # 同表列也可能共享值域并造成字段混用，例如 city/district。对同一 table_group
    # 的物理分区表只检查一个代表表，避免重复扫描同构分区。
    for table, cols in table_columns.items():
        if not _is_same_table_overlap_representative(table, representative_tables, table_group_memberships):
            stats["same_table_group_duplicate_skipped"] += len(cols) * (len(cols) - 1) // 2
            continue
        for col1, col2 in combinations(cols, 2):
            if not add_pair(col1, col2):
                return candidates, stats

    return candidates, stats


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
    """Cheap type gate shared by every storage-backed database dialect."""

    family1 = _type_family(col1.get("data_type"))
    family2 = _type_family(col2.get("data_type"))

    if family1 in {"numeric", "temporal", "boolean"} and family1 == family2:
        return False
    if not family1 or not family2:
        return True
    return family1 == family2


def _type_family(data_type: str | None) -> str:
    text = str(data_type or "").lower()
    if not text:
        return ""
    if any(token in text for token in ("bool",)):
        return "boolean"
    if any(token in text for token in NUMERIC_TYPES) or "number" in text:
        return "numeric"
    if any(token in text for token in TEMPORAL_TYPES):
        return "temporal"
    if any(token in text for token in ("char", "string", "text", "varchar")):
        return "text"
    return "other"


def _detect_column_overlaps_sql(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
) -> List[Dict]:
    """Detect value-domain overlaps with SQL-side DISTINCT/intersection counts."""

    overlaps: list[Dict] = []
    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            for col1, col2 in candidates:
                overlap_result = _calculate_overlap_sql(cursor, col1, col2, dialect)
                if not overlap_result or overlap_result["card_overlap"] == 0:
                    continue
                overlaps.append(_pair_overlap_payload(col1, col2, overlap_result))
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Could not open database for SQL overlap detection: %s", exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    overlaps.sort(key=lambda item: (-item["stats"]["card_overlap"], -item["stats"]["jaccard"]))
    return overlaps


def _open_db_connection(db_connect, *, readonly: bool = True):
    try:
        return db_connect(readonly=readonly)
    except TypeError:
        return db_connect()


def _calculate_overlap_sql(cursor, col1: Dict, col2: Dict, dialect: str) -> Optional[Dict]:
    try:
        query = _overlap_count_sql(col1, col2, dialect)
        cursor.execute(query)
        row = cursor.fetchone()
        if not row:
            return None
        card_1 = int(_row_value(row, 0) or 0)
        card_2 = int(_row_value(row, 1) or 0)
        card_overlap = int(_row_value(row, 2) or 0)
        if card_overlap == 0:
            return None

        sample_values: list[str] = []
        if _needs_intersection_sample(card_1, card_2, card_overlap):
            cursor.execute(_overlap_sample_sql(col1, col2, dialect))
            sample_values = [_normalize_value(_row_value(sample_row, 0)) for sample_row in cursor.fetchall()]

        if _is_disabled_overlap_stats(col1, col2, card_1, card_2, card_overlap, sample_values):
            return None

        union = card_1 + card_2 - card_overlap
        jaccard = card_overlap / union if union > 0 else 0.0
        return {
            "card_overlap": card_overlap,
            "jaccard": round(jaccard, 4),
            "cardinality_A": card_1,
            "cardinality_B": card_2,
            "coverage_A_in_B": round(card_overlap / card_1, 4) if card_1 > 0 else 0.0,
            "coverage_B_in_A": round(card_overlap / card_2, 4) if card_2 > 0 else 0.0,
        }
    except Exception as e:
        logger.debug("Could not calculate SQL overlap for %s <-> %s: %s", col1["entity_name"], col2["entity_name"], e)
        return None


def _overlap_count_sql(col1: Dict, col2: Dict, dialect: str) -> str:
    left_table = _qualified_table_sql(col1, dialect)
    right_table = _qualified_table_sql(col2, dialect)
    left_col = _quote_identifier(col1["column"], dialect)
    right_col = _quote_identifier(col2["column"], dialect)
    return f"""
WITH
  a AS (
    SELECT DISTINCT {left_col} AS v
    FROM {left_table}
    WHERE {left_col} IS NOT NULL
  ),
  b AS (
    SELECT DISTINCT {right_col} AS v
    FROM {right_table}
    WHERE {right_col} IS NOT NULL
  ),
  i AS (
    SELECT a.v AS v
    FROM a
    INNER JOIN b ON a.v = b.v
  )
SELECT
  (SELECT COUNT(*) FROM a) AS cardinality_a,
  (SELECT COUNT(*) FROM b) AS cardinality_b,
  (SELECT COUNT(*) FROM i) AS card_overlap
"""


def _overlap_sample_sql(col1: Dict, col2: Dict, dialect: str) -> str:
    left_table = _qualified_table_sql(col1, dialect)
    right_table = _qualified_table_sql(col2, dialect)
    left_col = _quote_identifier(col1["column"], dialect)
    right_col = _quote_identifier(col2["column"], dialect)
    return f"""
WITH
  a AS (
    SELECT DISTINCT {left_col} AS v
    FROM {left_table}
    WHERE {left_col} IS NOT NULL
  ),
  b AS (
    SELECT DISTINCT {right_col} AS v
    FROM {right_table}
    WHERE {right_col} IS NOT NULL
  )
SELECT a.v AS v
FROM a
INNER JOIN b ON a.v = b.v
LIMIT {INTERSECTION_SAMPLE_LIMIT}
"""


def _row_value(row, index: int):
    if isinstance(row, dict):
        return list(row.values())[index]
    return row[index]


def _needs_intersection_sample(card_1: int, card_2: int, card_overlap: int) -> bool:
    if card_overlap <= 0:
        return False
    coverage_1 = card_overlap / card_1 if card_1 else 0.0
    coverage_2 = card_overlap / card_2 if card_2 else 0.0
    return max(coverage_1, coverage_2) < SHORT_CODE_MAX_COVERAGE or (card_1 <= 2 and card_2 <= 2)


def _is_disabled_overlap_stats(
    col1: Dict,
    col2: Dict,
    card_1: int,
    card_2: int,
    card_overlap: int,
    intersection_sample: list[str],
) -> bool:
    if _below_overlap_threshold_counts(card_1, card_2, card_overlap):
        return True
    if card_1 <= 1 or card_2 <= 1:
        return True
    if _is_boolean_type(col1.get("data_type")) and _is_boolean_type(col2.get("data_type")):
        return True
    if card_1 <= 2 and card_2 <= 2 and intersection_sample and _is_boolean_domain_sample(intersection_sample):
        return True
    if _is_short_code_collision_stats(card_1, card_2, card_overlap, intersection_sample):
        return True
    if _is_numeric_type(col1.get("data_type")) and _is_numeric_type(col2.get("data_type")):
        return True
    if _is_temporal_type(col1.get("data_type")) and _is_temporal_type(col2.get("data_type")):
        return True
    return False


def _pair_overlap_payload(col1: Dict, col2: Dict, overlap_result: Dict) -> Dict:
    return {
        "from_table": col1["table"],
        "from_table_name": col1.get("table_name") or col1["table"],
        "from_column": col1["column"],
        "from_ref": col1["entity_name"],
        "from_type": col1["data_type"],
        "to_table": col2["table"],
        "to_table_name": col2.get("table_name") or col2["table"],
        "to_column": col2["column"],
        "to_ref": col2["entity_name"],
        "to_type": col2["data_type"],
        "sources": ["value_domain"],
        "stats": {
            "card_overlap": overlap_result["card_overlap"],
            "jaccard": overlap_result["jaccard"],
            "cardinality_A": overlap_result["cardinality_A"],
            "cardinality_B": overlap_result["cardinality_B"],
            "coverage_A_in_B": overlap_result["coverage_A_in_B"],
            "coverage_B_in_A": overlap_result["coverage_B_in_A"],
        },
    }


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
    for col in sorted(columns, key=lambda item: (item["table"], item["column"], item["entity_name"])):
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
            ("min_card_overlap", "card_overlap"),
            ("max_card_overlap", "card_overlap"),
            ("min_jaccard", "jaccard"),
            ("max_jaccard", "jaccard"),
        ):
            values = [value.get(source) for value in stat_values if value.get(source) is not None]
            if not values:
                continue
            updated[target] = min(values) if target.startswith("min_") else max(values)
    return updated


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


def _below_overlap_threshold_counts(card_1: int, card_2: int, card_overlap: int) -> bool:
    if card_overlap >= MIN_OVERLAP_VALUES:
        return False
    coverage_1 = card_overlap / card_1 if card_1 else 0.0
    coverage_2 = card_overlap / card_2 if card_2 else 0.0
    return max(coverage_1, coverage_2) < MIN_OVERLAP_COVERAGE_OVERRIDE


def _is_boolean_type(data_type: str | None) -> bool:
    return "bool" in str(data_type or "").lower()


def _is_boolean_domain_sample(values: list[str]) -> bool:
    normalized = {value for value in values if value}
    return 0 < len(normalized) <= 2 and normalized.issubset(BOOLEAN_VALUES)


def _is_short_code_collision_stats(
    card_1: int,
    card_2: int,
    card_overlap: int,
    intersection_sample: list[str],
) -> bool:
    normalized = [value for value in intersection_sample if value]
    if not normalized:
        return False

    short_code_count = sum(1 for value in normalized if _is_short_code_value(value))
    short_code_ratio = short_code_count / len(normalized)
    if short_code_ratio < SHORT_CODE_RATIO_THRESHOLD:
        return False

    coverage_1 = card_overlap / card_1 if card_1 else 0.0
    coverage_2 = card_overlap / card_2 if card_2 else 0.0
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
    table_name = column.get("table_name") or column["table"]
    raw_table = table_name.split("--")[-1] if "--" in table_name else table_name
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
