"""DB Basic Generator - 数据库实体展开器

职责：
- 匹配所有 *.db 节点
- 读取 SQLite 数据库结构
- 在 _entity/ 下创建 .table/ 和 .col/ 子节点

独立执行：
    python -m extractor.db_basic ./my_data
"""
import os
import logging
from datetime import datetime
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def _normalize_type(sql_type: str) -> str:
    """标准化SQL类型"""
    sql_type_upper = (sql_type or "").upper()
    if any(t in sql_type_upper for t in ['INT', 'SERIAL', 'BIGINT']):
        return "INT"
    elif any(t in sql_type_upper for t in ['REAL', 'FLOAT', 'DOUBLE', 'DECIMAL']):
        return "REAL"
    elif any(t in sql_type_upper for t in ['TEXT', 'CLOB', 'CHAR', 'VARCHAR']):
        return "TEXT"
    elif any(t in sql_type_upper for t in ['BLOB', 'BINARY']):
        return "BLOB"
    elif 'JSON' in sql_type_upper:
        return "JSON"
    elif 'BOOLEAN' in sql_type_upper or 'BOOL' in sql_type_upper:
        return "BOOL"
    elif any(t in sql_type_upper for t in ['DATE', 'TIME']):
        return "DATETIME"
    return "TEXT"


def generate(storage: VFSStorage) -> None:
    """为所有数据库节点展开实体结构"""
    logger.info("=== Generating DB entities ===")

    for node in storage.find_nodes("*.db"):
        try:
            _expand_database(node, storage)
        except Exception as e:
            logger.warning(f"Failed to expand DB {node.name}: {e}")


def _expand_database(node: NodeRef, storage: VFSStorage) -> None:
    """展开数据库为表和列实体

    结构：
    _entity/
        [表名].table/
        [表名].[列名].[类型].col/
        [视图名].view/
        [视图名].[列名].[类型].col/
    """
    meta = storage.read_meta(node)
    rel_path = meta.get("path") if meta else None
    db_path = storage.resolve_path(rel_path) if rel_path else None
    if not db_path or not os.path.exists(db_path):
        return

    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建 _entity 目录
    entity_rel = os.path.join(node.rel_path, "_entity")
    entity_node = NodeRef(entity_rel, storage.pontis_root)
    storage.ensure_dir(entity_node.full_path)

    # 获取表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    for (table_name,) in cursor.fetchall():
        safe_name = table_name.replace("/", "_").replace("\\", "_")

        # 表节点
        table_rel = os.path.join(entity_rel, f"{safe_name}.table")
        table_node = NodeRef(table_rel, storage.pontis_root)
        storage.ensure_dir(table_node.full_path)
        storage.write_meta(table_node, {"created_at": datetime.now().isoformat()})

        # 列节点
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        for col in cursor.fetchall():
            col_name = col[1]
            col_type = _normalize_type(col[2])
            safe_col = col_name.replace("/", "_").replace("\\", "_")

            col_rel = os.path.join(entity_rel, f"{safe_name}.{safe_col}.{col_type}.col")
            col_node = NodeRef(col_rel, storage.pontis_root)
            storage.ensure_dir(col_node.full_path)
            storage.write_meta(col_node, {
                "created_at": datetime.now().isoformat(),
                "source_table": table_name,
            })

        logger.info(f"  Entity: {node.name}/_entity/{safe_name}.table")

    # 获取视图
    cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
    for (view_name,) in cursor.fetchall():
        safe_name = view_name.replace("/", "_").replace("\\", "_")

        view_rel = os.path.join(entity_rel, f"{safe_name}.view")
        view_node = NodeRef(view_rel, storage.pontis_root)
        storage.ensure_dir(view_node.full_path)
        storage.write_meta(view_node, {"created_at": datetime.now().isoformat()})

        try:
            cursor.execute(f'PRAGMA table_info("{view_name}")')
            for col in cursor.fetchall():
                col_name = col[1]
                col_type = _normalize_type(col[2])
                safe_col = col_name.replace("/", "_").replace("\\", "_")

                col_rel = os.path.join(entity_rel, f"{safe_name}.{safe_col}.{col_type}.col")
                col_node = NodeRef(col_rel, storage.pontis_root)
                storage.ensure_dir(col_node.full_path)
                storage.write_meta(col_node, {
                    "created_at": datetime.now().isoformat(),
                    "source_view": view_name,
                })
        except Exception:
            pass

        logger.info(f"  Entity: {node.name}/_entity/{safe_name}.view")

    conn.close()


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="Generate DB entities")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    pontis_path = os.path.join(os.path.abspath(args.target), ".pontis")
    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)
    generate(VFSStorage(pontis_path))
    print("Done.")


if __name__ == '__main__':
    main()
