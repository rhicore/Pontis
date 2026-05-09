"""DB Column TopK Generator - 数据库列TopK值生成器

职责：
- 匹配所有 *.db 下的列节点
- 将topk数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.db_column_topk ./my_data
"""
import logging
from typing import Optional, List, Dict, Any
from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, get_entity_meta, set_entity_meta

logger = logging.getLogger(__name__)



def generate(workspace: Workspace, k: int = 5) -> None:
    """为所有DB列生成TopK值"""
    logger.info("=== Generating DB column TopK values ===")

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
                        _generate_for_column(col_ref, db_ref, table_ref, workspace, k)
                    except Exception as e:
                        logger.warning(f"Failed to generate topk for {col_ref}: {e}")


def _generate_for_column(col_ref: str, db_ref: str, table_ref: str,
                         workspace: Workspace, k: int) -> bool:
    """为单个列生成topk数据并存入meta根级别"""
    meta = get_entity_meta(workspace, col_ref)
    if not meta:
        return False

    if "topk" in meta:
        return False

    col_name = meta.get("name", col_ref)
    table_name = table_ref
    db_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": db_ref})
    db_meta = db_meta_rows[0].get("n") if db_meta_rows else None
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    if not workspace.data_exists(db_rel):
        return False

    topk = _calculate_topk(db_rel, table_name, col_name, k, workspace)
    if topk is None:
        return False

    set_entity_meta(workspace, col_ref, {"topk": topk})
    logger.info(f"  TopK added: {col_ref} ({len(topk)} items)")
    return True


def _calculate_topk(db_rel: str, table: str, column: str, k: int, workspace: Workspace) -> Optional[List[Dict[str, Any]]]:
    """计算最常见的K个值"""
    try:
        with workspace.open_db(db_rel) as conn:
            cursor = conn.cursor()

            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            total_rows = cursor.fetchone()[0]

            if total_rows == 0:
                return []

            cursor.execute(f'''
                SELECT "{column}", COUNT(*) as cnt
                FROM "{table}"
                WHERE "{column}" IS NOT NULL
                GROUP BY "{column}"
                ORDER BY cnt DESC
                LIMIT {k}
            ''')

            rows = cursor.fetchall()

            topk = []
            for value, count in rows:
                if isinstance(value, bytes):
                    value = f"<BLOB:{len(value)}bytes>"

                topk.append({
                    "value": value,
                    "count": count,
                    "percentage": round((count / total_rows) * 100, 2)
                })

            return topk

    except Exception as e:
        logger.debug(f"Could not calculate topk: {e}")
        return None
