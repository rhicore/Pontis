"""Column Statistics Generator - 列统计生成器

职责：
- 匹配所有 *.db 下的列节点
- 读取父DB的source_path
- 计算统计数据并追加到_meta.yml

独立执行：
    python -m extractor.db_column_stats ./my_data
"""
import logging
from typing import Optional
from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, get_entity_meta, set_entity_meta

logger = logging.getLogger(__name__)



def generate(workspace: Workspace) -> None:
    """为所有列节点生成统计信息"""
    logger.info("=== Generating column statistics ===")

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
                        logger.warning(f"Failed to generate stats for {col_ref}: {e}")


def _generate_for_column(col_ref: str, db_ref: str, table_ref: str,
                         workspace: Workspace) -> bool:
    """为单个列生成统计"""
    meta = get_entity_meta(workspace, col_ref)
    if not meta:
        return False

    if "cardinality" in meta:
        return False

    col_name = meta.get("name", col_ref)
    table_name = table_ref
    data_type = meta.get("col_type", "")
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    if not workspace.data_exists(db_rel):
        return False

    stats = _calculate_stats(db_rel, table_name, col_name, data_type, workspace)
    if not stats:
        return False

    set_entity_meta(workspace, col_ref, stats)
    logger.info(f"  Stats generated: {col_ref} (cardinality={stats.get('cardinality')})")
    return True


def _calculate_stats(db_rel: str, table: str, column: str, data_type: str, workspace: Workspace) -> Optional[dict]:
    """从数据库计算统计"""
    try:
        with workspace.open_db(db_rel) as conn:
            cursor = conn.cursor()

            stats = {}

            # Row count
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            total_rows = cursor.fetchone()[0]

            if total_rows == 0:
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

            return stats

    except Exception as e:
        logger.debug(f"Could not calculate stats: {e}")
        return None
