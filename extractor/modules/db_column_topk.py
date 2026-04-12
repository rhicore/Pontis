"""DB Column TopK Generator - 数据库列TopK值生成器

职责：
- 匹配所有 *.db 下的 *.*.*.col 节点
- 将topk数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.db_column_topk ./my_data
"""
import os
import logging
from typing import Optional, List, Dict, Any
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store, k: int = 5) -> None:
    """为所有DB列生成TopK值"""
    logger.info("=== Generating DB column TopK values ===")

    for ref in store.find_nodes("*.db::*.*.*.col"):
        try:
            _generate_for_column(ref, store, k)
        except Exception as e:
            logger.warning(f"Failed to generate topk for {ref}: {e}")


def _generate_for_column(ref: str, store: Store,
                         k: int) -> bool:
    """为单个列生成topk数据并存入meta根级别"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    # 检查是否已处理
    if "topk" in meta:
        return False

    # 解析实体名: [表名].[列名].[类型].col
    col_parts = entity_name.replace(".col", "").split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]

    # 获取DB源路径
    db_path = os.path.join(store.project_path, store.get_meta(path).get("path", ""))
    if not db_path:
        return False

    # 计算TopK
    topk = _calculate_topk(db_path, table_name, col_name, k)
    if topk is None:
        return False

    store.set_meta(ref, {"topk": topk})
    logger.info(f"  TopK added: {ref} ({len(topk)} items)")
    return True


def _calculate_topk(db_path: str, table: str, column: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """计算最常见的K个值"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        total_rows = cursor.fetchone()[0]

        if total_rows == 0:
            conn.close()
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
        conn.close()

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
