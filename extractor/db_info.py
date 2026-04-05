"""DB Info Generator - 数据库信息生成器

职责：
- 匹配 *.db 节点
- 添加数据库级元信息（表数量、视图数量、文件大小等）

独立执行：
    python -m extractor.db_info ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有DB节点生成信息"""
    logger.info("=== Generating DB info ===")

    for node in storage.find_nodes("*.db"):
        try:
            _generate_for_db(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate info for {node.name}: {e}")


def _generate_for_db(node: NodeRef, storage: VFSStorage) -> bool:
    """为单个DB生成信息"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "table_count" in meta:
        return False

    rel_path = meta.get("path")
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
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
        meta.update({
            "table_count": table_count,
            "view_count": view_count,
            "index_count": index_count,
            "file_size": file_size,
        })
        storage.write_meta(node, meta)

        logger.info(f"  DB info: {node.rel_path} ({table_count} tables, {view_count} views)")
        return True

    except Exception as e:
        logger.debug(f"Could not get DB info: {e}")
        return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate DB info")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
