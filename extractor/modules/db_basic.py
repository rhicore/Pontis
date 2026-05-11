"""DB Basic Generator - 数据库实体展开

职责：
1. 通过 Cypher 发现所有数据库文件
2. 提取库级属性（table_count/view_count/index_count），写入时自动实体化
3. 展开表/视图/列实体
"""
import os
import logging
from datetime import datetime
from typing import List
from storage.workspace import Workspace
from extractor.modules.utils.refs import db_column_ref, db_table_ref, db_view_ref
from extractor.modules.utils.src import file_exists, open_sqlite_db

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


def _discover_db_files(workspace: Workspace) -> List[str]:
    """通过统一索引发现所有数据库文件（含虚拟实体）。"""
    exts = ('.db', '.sqlite', '.sqlite3', '.duckdb')
    results = []
    seen = set()
    rows = workspace.cypher("MATCH (n) RETURN n")
    for row in rows:
        props = row.get("n", {})
        name = props.get("name", "")
        if not any(name.endswith(ext) for ext in exts):
            continue
        rel_path = props.get("path", name)
        if rel_path not in seen:
            seen.add(rel_path)
            results.append(rel_path)
    return results


def generate(workspace: Workspace) -> None:
    """发现所有数据库文件，写入属性并展开实体"""
    logger.info("=== Generating DB entities ===")

    count = 0
    for path in _discover_db_files(workspace):
        try:
            _process_database(path, workspace)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to process DB {path}: {e}")

    logger.info(f"  Processed {count} database files")


def _process_database(rel_path: str, workspace: Workspace) -> None:
    """处理单个数据库：写入库属性（自动实体化文件节点）+ 展开表/视图/列实体"""
    if not file_exists(workspace, rel_path):
        return
    basename = os.path.basename(rel_path)

    with open_sqlite_db(workspace, rel_path) as conn:
        cursor = conn.cursor()

        # 写入库级属性（自动实体化文件节点）
        cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        table_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='view'")
        view_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='index'")
        index_count = cursor.fetchone()[0]
        workspace.cypher('MATCH (n {name: $name}) SET n += $props',
                      params={"name": basename, "props": {
                          "created_at": datetime.now().isoformat(),
                          "table_count": table_count,
                          "view_count": view_count,
                          "index_count": index_count,
                          "path": rel_path,
                      }})
        logger.info(f"  DB properties: {basename} ({table_count} tables, {view_count} views)")

        # 获取表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (table_name,) in cursor.fetchall():
            safe_name = table_name.replace("/", "_").replace("\\", "_")

            cursor.execute(f'PRAGMA table_info("{table_name}")')
            columns = cursor.fetchall()
            pk_col = next((col[1] for col in columns if col[5] == 1), None)

            ts = datetime.now().isoformat()
            table_ref = db_table_ref(basename, safe_name)
            workspace.cypher(
                f'CREATE (t:table {{ref: "{table_ref}", created_at: "{ts}", primary_key: "{pk_col or ""}"}})'
            )

            for col in columns:
                col_name = col[1]
                col_type = _normalize_type(col[2])
                safe_col = col_name.replace("/", "_").replace("\\", "_")
                col_ref = db_column_ref(basename, safe_name, safe_col)
                workspace.cypher(
                    f'CREATE (c:col:{col_type} {{ref: "{col_ref}", created_at: "{ts}", col_type: "{col_type}"}})'
                )

            logger.info(f"  Entity: {safe_name}")

        # 获取视图
        cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
        for (view_name,) in cursor.fetchall():
            safe_name = view_name.replace("/", "_").replace("\\", "_")

            ts = datetime.now().isoformat()
            view_ref = db_view_ref(basename, safe_name)
            workspace.cypher(f'CREATE (v:view {{ref: "{view_ref}", created_at: "{ts}"}})')

            try:
                cursor.execute(f'PRAGMA table_info("{view_name}")')
                for col in cursor.fetchall():
                    col_name = col[1]
                    col_type = _normalize_type(col[2])
                    safe_col = col_name.replace("/", "_").replace("\\", "_")
                    col_ref = db_column_ref(basename, safe_name, safe_col)
                    workspace.cypher(
                        f'CREATE (c:col:{col_type} {{ref: "{col_ref}", created_at: "{ts}", col_type: "{col_type}", source_view: "{view_name}"}})'
                    )
            except Exception:
                pass

            logger.info(f"  Entity: {safe_name}")
