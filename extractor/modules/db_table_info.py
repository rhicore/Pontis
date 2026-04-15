"""DB Table Info Generator - 数据库表信息生成器

职责：
- 匹配 *.db/_entity/*.table 节点
- 添加表级元信息（行数、列数、主键等）

独立执行：
    python -m extractor.db_table_info ./my_data
"""
import os
import logging
from storage import Store

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store) -> None:
    """为所有表节点生成信息"""
    logger.info("=== Generating table info ===")

    for ext in DB_EXTENSIONS:
        for ref in store.find_nodes(f"{ext}::*.table"):
            try:
                _generate_for_table(ref, store)
            except Exception as e:
                logger.warning(f"Failed to generate info for {ref}: {e}")


def _generate_for_table(ref: str, store: Store) -> bool:
    """为单个表生成信息"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    if "row_count" in meta:
        return False

    table_name = entity_name.replace(".table", "")
    db_path = os.path.join(store.project_path, store.get_meta(path).get("path", ""))
    if not db_path:
        return False

    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 获取行数
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        row_count = cursor.fetchone()[0]

        # 获取列信息
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = cursor.fetchall()
        column_count = len(columns)

        # 获取主键
        pk_columns = [col[1] for col in columns if col[5] == 1]

        conn.close()

        # 更新meta
        store.set_meta(ref, {
            "row_count": row_count,
            "column_count": column_count,
            "primary_key": pk_columns[0] if pk_columns else None,
        })

        logger.info(f"  Table info: {ref} ({row_count} rows, {column_count} cols)")
        return True

    except Exception as e:
        logger.debug(f"Could not get table info: {e}")
        return False
