"""Approximate Column Statistics Generator - 列近似统计生成器

职责：
- 匹配所有 *.db 下的列节点
- 使用一趟列扫描计算精确 null / min/max / avg 等轻量统计
- 使用 datasketches CPC sketch 近似估算 cardinality

独立执行：
    python -m extractor.db_column_stats_approx ./my_data
"""

import logging
from typing import Optional

from datasketches import cpc_sketch

from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, get_entity_meta, set_entity_meta
from extractor.modules.utils.src import file_exists, open_sqlite_db

logger = logging.getLogger(__name__)

_NUMERIC_TYPES = {"INT", "INTEGER", "REAL", "FLOAT"}
_TEXT_TYPES = {"TEXT", "VARCHAR", "CHAR"}
_CPC_LG_K = 11


def generate(workspace: Workspace) -> None:
    """为所有列节点生成近似统计信息。"""
    logger.info("=== Generating approximate column statistics ===")

    for ext_suffix in [".db", ".sqlite", ".sqlite3", ".duckdb"]:
        db_rows = workspace.cypher(f"MATCH (n) WHERE n.name ENDS WITH '{ext_suffix}' RETURN n")
        for db_row in db_rows:
            db_ref = db_row["n"]["name"]
            tbl_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t:table) RETURN t')
            for tbl_row in tbl_rows:
                table_ref = tbl_row["t"]["name"]
                col_rows = workspace.cypher(f'MATCH (d {{name: "{db_ref}"}})--(t {{name: "{table_ref}"}})--(c:col) RETURN c')
                for col_row in col_rows:
                    col_name = col_row["c"]["name"]
                    col_ref = db_column_ref(db_ref, table_ref, col_name)
                    try:
                        _generate_for_column(col_ref, db_ref, table_ref, workspace)
                    except Exception as e:
                        logger.warning(f"Failed to generate approximate stats for {col_ref}: {e}")


def _generate_for_column(col_ref: str, db_ref: str, table_ref: str, workspace: Workspace) -> bool:
    meta = get_entity_meta(workspace, col_ref)
    if not meta:
        return False

    # 允许老的精确结果存在；如果已经有近似 cardinality 也不重复跑。
    if meta.get("cardinality_method") == "cpc_sketch":
        return False

    col_name = meta.get("name", col_ref)
    data_type = meta.get("col_type", "")
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    if not file_exists(workspace, db_rel):
        return False

    stats = _calculate_stats_approx(db_rel, table_ref, col_name, data_type, workspace)
    if not stats:
        return False

    set_entity_meta(workspace, col_ref, stats)
    logger.info(
        "  Approx stats: %s (cardinality≈%s)",
        col_ref,
        stats.get("cardinality"),
    )
    return True


def _calculate_stats_approx(
    db_rel: str,
    table: str,
    column: str,
    data_type: str,
    workspace: Workspace,
) -> Optional[dict]:
    try:
        with open_sqlite_db(workspace, db_rel) as conn:
            cursor = conn.cursor()
            cursor.execute(f'SELECT "{column}" FROM "{table}"')

            sketch = cpc_sketch(_CPC_LG_K)
            total_rows = 0
            null_count = 0

            numeric_count = 0
            numeric_sum = 0.0
            min_value = None
            max_value = None

            text_count = 0
            text_len_sum = 0
            min_length = None
            max_length = None

            for (value,) in cursor:
                total_rows += 1
                if value is None:
                    null_count += 1
                    continue

                if isinstance(value, (int, float, str)):
                    sketch.update(value)
                else:
                    sketch.update(str(value))

                if data_type in _NUMERIC_TYPES:
                    try:
                        num = float(value)
                    except (TypeError, ValueError):
                        continue
                    numeric_count += 1
                    numeric_sum += num
                    min_value = num if min_value is None else min(min_value, num)
                    max_value = num if max_value is None else max(max_value, num)
                elif data_type in _TEXT_TYPES:
                    text = str(value)
                    text_len = len(text)
                    text_count += 1
                    text_len_sum += text_len
                    min_length = text_len if min_length is None else min(min_length, text_len)
                    max_length = text_len if max_length is None else max(max_length, text_len)

            if total_rows == 0:
                return {
                    "cardinality": 0,
                    "cardinality_lower_bound": 0,
                    "cardinality_upper_bound": 0,
                    "cardinality_method": "cpc_sketch",
                    "null_count": 0,
                    "null_percentage": 0.0,
                }

            stats = {
                "cardinality": int(round(sketch.get_estimate())),
                "cardinality_lower_bound": int(round(sketch.get_lower_bound(1))),
                "cardinality_upper_bound": int(round(sketch.get_upper_bound(1))),
                "cardinality_method": "cpc_sketch",
                "null_count": null_count,
                "null_percentage": round((null_count / total_rows) * 100, 2),
            }

            if data_type in _NUMERIC_TYPES and numeric_count > 0:
                stats["min_value"] = _normalize_number(min_value)
                stats["max_value"] = _normalize_number(max_value)
                stats["mean_value"] = round(numeric_sum / numeric_count, 4)
            elif data_type in _TEXT_TYPES and text_count > 0:
                stats["min_length"] = min_length
                stats["max_length"] = max_length
                stats["avg_length"] = round(text_len_sum / text_count, 2)

            return stats
    except Exception as e:
        logger.debug(f"Could not calculate approximate stats: {e}")
        return None


def _normalize_number(value):
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value
