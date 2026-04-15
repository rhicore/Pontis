"""DB Info Generator - 数据库信息生成器

职责：
- 匹配 *.db 节点
- 添加数据库级元信息（表数量、视图数量、文件大小等）

独立执行：
    python -m extractor.db_info ./my_data
"""
import os
import logging
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有DB节点生成信息"""
    logger.info("=== Generating DB info ===")

    for pattern in ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]:
        for path in store.find_nodes(pattern):
            try:
                _generate_for_db(path, store)
            except Exception as e:
                logger.warning(f"Failed to generate info for {path}: {e}")


def _generate_for_db(path: str, store: Store) -> bool:
    """为单个DB生成信息"""
    meta = store.get_meta(path)
    if not meta:
        return False

    # 跳过已处理的
    if "table_count" in meta:
        return False

    db_path = os.path.join(store.project_path, meta["path"]) if meta.get("path") else None
    if not db_path:
        return False

    # 获取数据库统计
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 获取表数量
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        table_count = cursor.fetchone()[0]

        # 获取视图数量
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
        view_count = cursor.fetchone()[0]

        # 获取索引数量
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'")
        index_count = cursor.fetchone()[0]

        conn.close()

        # 获取文件大小
        file_size = os.path.getsize(db_path)

        # 更新meta
        store.set_meta(path, {
            "table_count": table_count,
            "view_count": view_count,
            "index_count": index_count,
            "file_size": file_size,
        })

        logger.info(f"  DB info: {path} ({table_count} tables, {view_count} views)")
        return True

    except Exception as e:
        logger.debug(f"Could not get DB info: {e}")
        return False
