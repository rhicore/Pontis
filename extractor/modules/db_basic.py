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
    """为所有数据库节点展开实体结构"""
    logger.info("=== Generating DB entities ===")

    for path in store.find_nodes("*.db"):
        try:
            _expand_database(path, store)
        except Exception as e:
            logger.warning(f"Failed to expand DB {path}: {e}")


def _expand_database(path: str, store: Store) -> None:
    """展开数据库为表和列实体

    结构：
    _entity/
        [表名].table/
        [表名].[列名].[类型].col/
        [视图名].view/
        [视图名].[列名].[类型].col/
    """
    meta = store.get_meta(path)
    db_path = os.path.join(store.project_path, meta["path"]) if meta and meta.get("path") else None
    if not db_path:
        return

    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 获取表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    for (table_name,) in cursor.fetchall():
        safe_name = table_name.replace("/", "_").replace("\\", "_")

        # 创建表实体
        store.create_node(f"{path}::{safe_name}.table",
                            meta={"created_at": datetime.now().isoformat()})

        # 创建列实体 + 边
        col_edges = []
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        for col in cursor.fetchall():
            col_name = col[1]
            col_type = _normalize_type(col[2])
            safe_col = col_name.replace("/", "_").replace("\\", "_")

            col_entity_name = f"{safe_name}.{safe_col}.{col_type}.col"
            store.create_node(f"{path}::{col_entity_name}",
                                meta={"created_at": datetime.now().isoformat(),
                                      "source_table": table_name})

            col_edges.append({
                "from": f"{path}::{safe_name}.table",
                "type": "columns",
                "to": f"{path}::{col_entity_name}",
            })

        if col_edges:
            store.add_edges(col_edges)

        logger.info(f"  Entity: {path}/_entity/{safe_name}.table")

    # 获取视图
    cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
    for (view_name,) in cursor.fetchall():
        safe_name = view_name.replace("/", "_").replace("\\", "_")

        store.create_node(f"{path}::{safe_name}.view",
                            meta={"created_at": datetime.now().isoformat()})

        view_col_edges = []
        try:
            cursor.execute(f'PRAGMA table_info("{view_name}")')
            for col in cursor.fetchall():
                col_name = col[1]
                col_type = _normalize_type(col[2])
                safe_col = col_name.replace("/", "_").replace("\\", "_")

                col_entity_name = f"{safe_name}.{safe_col}.{col_type}.col"
                store.create_node(f"{path}::{col_entity_name}",
                                    meta={"created_at": datetime.now().isoformat(),
                                          "source_view": view_name})

                view_col_edges.append({
                    "from": f"{path}::{safe_name}.view",
                    "type": "columns",
                    "to": f"{path}::{col_entity_name}",
                })
        except Exception:
            pass

        if view_col_edges:
            store.add_edges(view_col_edges)

        logger.info(f"  Entity: {path}/_entity/{safe_name}.view")

    conn.close()
