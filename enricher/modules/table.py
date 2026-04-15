"""数据库表/视图实体虚属性 — row_count, column_count"""
import os
import sqlite3
from typing import Dict, Callable, Optional


def row_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    table_name = entity_path.replace(".table", "").replace(".view", "").split("/")[-1]
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


def column_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    table_name = entity_path.replace(".table", "").replace(".view", "").split("/")[-1]
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f'PRAGMA table_info("{table_name}")')
        count = len(cur.fetchall())
        conn.close()
        return count
    except Exception:
        return None


PROPS: Dict[str, Dict[str, Callable]] = {
    ".table": {
        "row_count": row_count,
        "column_count": column_count,
    },
    ".view": {
        "row_count": row_count,
        "column_count": column_count,
    },
}
