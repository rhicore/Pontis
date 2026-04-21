"""DB Basic Generator - 数据库文件发现与实体展开

职责：
1. 通过 store.find_nodes() 发现所有数据库文件（含虚节点）
2. 为未索引的文件创建节点（含 _inode）
3. 展开表/视图/列实体
"""
import os
import logging
from datetime import datetime
from storage import Store

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


def generate(store: Store) -> None:
    """发现所有数据库文件，创建文件节点并展开实体"""
    logger.info("=== Generating DB entities ===")

    db_patterns = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb",
                    "**/*.db", "**/*.sqlite", "**/*.sqlite3", "**/*.duckdb"]
    count = 0
    for pattern in db_patterns:
        for path in store.find_nodes(pattern):
            if store.node_exists(path):
                continue  # 已索引，跳过
            try:
                _process_database(path, store)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to process DB {path}: {e}")

    logger.info(f"  Processed {count} new database files")


def _process_database(rel_path: str, store: Store) -> None:
    """处理单个数据库：创建文件节点 + 展开表/视图/列实体"""
    abs_path = os.path.join(store.project_path, rel_path)
    stat = os.stat(abs_path)

    # 创建文件节点
    meta = {
        "path": rel_path,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created_at": datetime.now().isoformat(),
    }
    store.create_node(rel_path, meta=meta)
    logger.info(f"  Created file node: {rel_path}")

    # 展开实体
    import sqlite3
    conn = sqlite3.connect(abs_path)
    cursor = conn.cursor()

    # 获取表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    for (table_name,) in cursor.fetchall():
        safe_name = table_name.replace("/", "_").replace("\\", "_")

        store.create_node(f"{rel_path}::{safe_name}.table",
                          meta={"created_at": datetime.now().isoformat()})

        col_edges = []
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        for col in cursor.fetchall():
            col_name = col[1]
            col_type = _normalize_type(col[2])
            safe_col = col_name.replace("/", "_").replace("\\", "_")

            col_entity_name = f"{safe_name}.{safe_col}.{col_type}.col"
            store.create_node(f"{rel_path}::{col_entity_name}",
                              meta={"created_at": datetime.now().isoformat()})

            col_edges.append({
                "a": f"{rel_path}::{safe_name}.table",
                "b": f"{rel_path}::{col_entity_name}",
            })

        if col_edges:
            store.add_edges(col_edges)

        logger.info(f"  Entity: {rel_path}::{safe_name}.table")

    # 获取视图
    cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
    for (view_name,) in cursor.fetchall():
        safe_name = view_name.replace("/", "_").replace("\\", "_")

        store.create_node(f"{rel_path}::{safe_name}.view",
                          meta={"created_at": datetime.now().isoformat()})

        view_col_edges = []
        try:
            cursor.execute(f'PRAGMA table_info("{view_name}")')
            for col in cursor.fetchall():
                col_name = col[1]
                col_type = _normalize_type(col[2])
                safe_col = col_name.replace("/", "_").replace("\\", "_")

                col_entity_name = f"{safe_name}.{safe_col}.{col_type}.col"
                store.create_node(f"{rel_path}::{col_entity_name}",
                                  meta={"created_at": datetime.now().isoformat(),
                                        "source_view": view_name})

                view_col_edges.append({
                    "a": f"{rel_path}::{safe_name}.view",
                    "b": f"{rel_path}::{col_entity_name}",
                })
        except Exception:
            pass

        if view_col_edges:
            store.add_edges(view_col_edges)

        logger.info(f"  Entity: {rel_path}::{safe_name}.view")

    conn.close()
