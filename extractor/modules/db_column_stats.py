"""Column Statistics Generator - 列统计生成器

职责：
- 匹配所有 *.db 下的列节点
- 读取父DB的source_path
- 计算统计数据并追加到_meta.yml

独立执行：
    python -m extractor.db_column_stats ./my_data
"""
import os
import logging
from typing import Optional
from storage import Store

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store) -> None:
    """为所有列节点生成统计信息"""
    logger.info("=== Generating column statistics ===")

    for ext in DB_EXTENSIONS:
        for db_ref in store.find_nodes(ext):
            for table_ref in store.find_nodes(f"{db_ref}::*:table"):
                for col_ref in store.find_nodes(f"{db_ref}::{table_ref}::*:col"):
                    try:
                        _generate_for_column(col_ref, db_ref, table_ref, store)
                    except Exception as e:
                        logger.warning(f"Failed to generate stats for {col_ref}: {e}")


def _generate_for_column(col_ref: str, db_ref: str, table_ref: str,
                         store: Store) -> bool:
    """为单个列生成统计"""
    meta = store.get_meta(col_ref)
    if not meta:
        return False

    if "cardinality" in meta:
        return False

    col_name = col_ref
    table_name = table_ref
    data_type = meta.get("col_type", "")
    db_meta = store.get_meta(db_ref)
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    db_path = os.path.join(store.project_path, db_rel)
    if not db_path or not os.path.isfile(db_path):
        return False

    stats = _calculate_stats(db_path, table_name, col_name, data_type)
    if not stats:
        return False

    store.set_meta(col_ref, stats)
    logger.info(f"  Stats generated: {col_ref} (cardinality={stats.get('cardinality')})")
    return True


def _calculate_stats(db_path: str, table: str, column: str, data_type: str) -> Optional[dict]:
    """从数据库计算统计"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        stats = {}

        # Row count
        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        total_rows = cursor.fetchone()[0]

        if total_rows == 0:
            conn.close()
            return {"cardinality": 0, "null_count": 0}

        # Cardinality
        cursor.execute(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}" WHERE "{column}" IS NOT NULL')
        stats["cardinality"] = cursor.fetchone()[0]

        # Null count
        cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL')
        null_count = cursor.fetchone()[0]
        stats["null_count"] = null_count
        stats["null_percentage"] = round((null_count / total_rows) * 100, 2)

        # Type-specific
        if data_type in ["INT", "INTEGER", "REAL", "FLOAT"]:
            cursor.execute(f'SELECT MIN("{column}"), MAX("{column}"), AVG("{column}") FROM "{table}" WHERE "{column}" IS NOT NULL')
            row = cursor.fetchone()
            if row:
                stats["min_value"] = row[0]
                stats["max_value"] = row[1]
                stats["mean_value"] = round(row[2], 4) if row[2] else None

        elif data_type in ["TEXT", "VARCHAR", "CHAR"]:
            cursor.execute(f'SELECT MIN(LENGTH("{column}")), MAX(LENGTH("{column}")), AVG(LENGTH("{column}")) FROM "{table}" WHERE "{column}" IS NOT NULL')
            row = cursor.fetchone()
            if row:
                stats["min_length"] = row[0]
                stats["max_length"] = row[1]
                stats["avg_length"] = round(row[2], 2) if row[2] else None

        conn.close()
        return stats

    except Exception as e:
        logger.debug(f"Could not calculate stats: {e}")
        return None
