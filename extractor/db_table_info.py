"""DB Table Info Generator - 数据库表信息生成器

职责：
- 匹配 *.db/*.table 节点
- 添加表级元信息（行数、列数、主键等）
- 列数从扁平结构的列节点计算（*.db/[表名].*.*.col）

独立执行：
    python -m extractor.db_table_info ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有表节点生成信息"""
    logger.info("=== Generating table info ===")

    for node in storage.find_nodes("*.db/*.table"):
        try:
            _generate_for_table(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate info for {node.name}: {e}")


def _generate_for_table(node: NodeRef, storage: VFSStorage) -> bool:
    """为单个表生成信息"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "row_count" in meta:
        return False

    # 解析路径
    path_parts = node.rel_path.split(os.sep)
    if len(path_parts) < 2:
        return False

    # 找到.db节点位置
    db_idx = -1
    for i, part in enumerate(path_parts):
        if part.endswith('.db'):
            db_idx = i
            break

    if db_idx == -1:
        return False

    db_rel_path = os.sep.join(path_parts[:db_idx+1])
    table_name = node.name.replace(".table", "")

    # 获取DB路径
    db_node = NodeRef(db_rel_path, node.pontis_root)
    db_meta = storage.read_meta(db_node)
    if not db_meta:
        return False

    rel_path = db_meta.get("path")
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
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
        meta.update({
            "row_count": row_count,
            "column_count": column_count,
            "primary_key": pk_columns[0] if pk_columns else None,
        })
        storage.write_meta(node, meta)

        logger.info(f"  Table info: {node.rel_path} ({row_count} rows, {column_count} cols)")
        return True

    except Exception as e:
        logger.debug(f"Could not get table info: {e}")
        return False


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate table info")
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
