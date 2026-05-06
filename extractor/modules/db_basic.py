"""DB Basic Generator - 数据库实体展开

职责：
1. 通过 store.find_nodes() 发现所有数据库文件（虚节点即可）
2. 提取库级属性（table_count/view_count/index_count），写入时自动实体化
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
    """发现所有数据库文件，写入属性并展开实体"""
    logger.info("=== Generating DB entities ===")

    db_patterns = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb",
                    "**/*.db", "**/*.sqlite", "**/*.sqlite3", "**/*.duckdb"]
    count = 0
    for pattern in db_patterns:
        for path in store.find_nodes(pattern):
            try:
                _process_database(path, store)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to process DB {path}: {e}")

    logger.info(f"  Processed {count} database files")


def _process_database(rel_path: str, store: Store) -> None:
    """处理单个数据库：写入库属性（自动实体化文件节点）+ 展开表/视图/列实体"""
    abs_path = os.path.join(store.project_path, rel_path)
    basename = os.path.basename(rel_path)

    import sqlite3
    conn = sqlite3.connect(abs_path)
    cursor = conn.cursor()

    # 写入库级属性（自动实体化文件节点）
    cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    table_count = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='view'")
    view_count = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='index'")
    index_count = cursor.fetchone()[0]
    store.set_meta(basename, {
        "created_at": datetime.now().isoformat(),
        "table_count": table_count,
        "view_count": view_count,
        "index_count": index_count,
    })
    logger.info(f"  DB properties: {basename} ({table_count} tables, {view_count} views)")

    # 获取表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    for (table_name,) in cursor.fetchall():
        safe_name = table_name.replace("/", "_").replace("\\", "_")

        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = cursor.fetchall()
        pk_col = next((col[1] for col in columns if col[5] == 1), None)

        table_ref = f"{basename}--{safe_name}"
        store.create_node(table_ref,
                          meta={"created_at": datetime.now().isoformat(),
                                "primary_key": pk_col},
                          labels=["table"])
        store.add_edges([{"a": basename, "b": table_ref}])

        for col in columns:
            col_name = col[1]
            col_type = _normalize_type(col[2])
            safe_col = col_name.replace("/", "_").replace("\\", "_")

            col_ref = f"{table_ref}--{safe_col}"
            store.create_node(col_ref,
                              meta={"created_at": datetime.now().isoformat(),
                                    "col_type": col_type},
                              labels=[f"col/{col_type}"])
            store.add_edges([{"a": table_ref, "b": col_ref}])

        logger.info(f"  Entity: {table_ref}")

    # 获取视图
    cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
    for (view_name,) in cursor.fetchall():
        safe_name = view_name.replace("/", "_").replace("\\", "_")

        view_ref = f"{basename}--{safe_name}"
        store.create_node(view_ref,
                          meta={"created_at": datetime.now().isoformat()},
                          labels=["view"])
        store.add_edges([{"a": basename, "b": view_ref}])

        try:
            cursor.execute(f'PRAGMA table_info("{view_name}")')
            for col in cursor.fetchall():
                col_name = col[1]
                col_type = _normalize_type(col[2])
                safe_col = col_name.replace("/", "_").replace("\\", "_")

                col_ref = f"{view_ref}--{safe_col}"
                store.create_node(col_ref,
                                  meta={"created_at": datetime.now().isoformat(),
                                        "col_type": col_type, "source_view": view_name},
                                  labels=[f"col/{col_type}"])
                store.add_edges([{"a": view_ref, "b": col_ref}])
        except Exception:
            pass

        logger.info(f"  Entity: {view_ref}")

    conn.close()
