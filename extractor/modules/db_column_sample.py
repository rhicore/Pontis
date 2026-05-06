"""DB Column Sample Generator - 数据库列采样生成器

职责：
- 匹配所有 *.db 下的列节点
- 将sample数据直接放入列节点的_meta.yml根级别

独立执行：
    python -m extractor.db_column_sample ./my_data
"""
import os
import logging
from typing import Optional, List, Any
from storage import Store

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store, sample_size: int = 10) -> None:
    """为所有DB列生成样本"""
    logger.info("=== Generating DB column samples ===")

    for ext in DB_EXTENSIONS:
        for db_ref in store.find_nodes(ext):
            for table_ref in store.find_nodes(f"{db_ref}::*:table"):
                for col_ref in store.find_nodes(f"{db_ref}::{table_ref}::*:col"):
                    try:
                        _generate_for_column(col_ref, db_ref, table_ref,
                                             store, sample_size)
                    except Exception as e:
                        logger.warning(f"Failed to generate sample for {col_ref}: {e}")


def _generate_for_column(col_ref: str, db_ref: str, table_ref: str,
                         store: Store, sample_size: int) -> bool:
    """为单个列生成sample数据并存入meta根级别"""
    meta = store.get_meta(col_ref)
    if not meta:
        return False

    if "sample" in meta:
        return False

    col_name = col_ref
    table_name = table_ref
    db_meta = store.get_meta(db_ref)
    db_rel = db_meta.get("path", db_ref) if db_meta else db_ref
    db_path = os.path.join(store.project_path, db_rel)
    if not db_path or not os.path.isfile(db_path):
        return False

    samples = _get_samples(db_path, table_name, col_name, sample_size)
    if samples is None:
        return False

    store.set_meta(col_ref, {"sample": samples})
    logger.info(f"  Sample added: {col_ref} ({len(samples)} items)")
    return True


def _get_samples(db_path: str, table: str, column: str, sample_size: int) -> Optional[List[Any]]:
    """从数据库获取样本"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(f'''
            SELECT DISTINCT "{column}"
            FROM "{table}"
            WHERE "{column}" IS NOT NULL
            LIMIT {sample_size}
        ''')

        rows = cursor.fetchall()
        conn.close()

        samples = []
        for row in rows:
            value = row[0]
            if isinstance(value, bytes):
                samples.append(f"<BLOB:{len(value)}bytes>")
            else:
                samples.append(value)

        return samples

    except Exception as e:
        logger.debug(f"Could not get samples: {e}")
        return None
