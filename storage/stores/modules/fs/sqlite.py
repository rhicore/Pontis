"""SQLite 虚属性 — 数据库文件 + 表/视图实体"""
import os
import sqlite3
from typing import Callable, Dict, Optional

from .file import COMMON_FILE_PROPS


# ── 数据库文件属性 ──

def table_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


def view_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


def index_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


# ── 表/视图实体属性 ──

def row_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    table_name = entity_path.split("/")[-1] if "/" in entity_path else entity_path
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
    table_name = entity_path.split("/")[-1] if "/" in entity_path else entity_path
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


DB_PROPS: Dict[str, Callable] = {
    **COMMON_FILE_PROPS,
    "table_count": table_count,
    "view_count": view_count,
    "index_count": index_count,
}

TABLE_PROPS: Dict[str, Callable] = {
    "row_count": row_count,
    "column_count": column_count,
}

VIEW_PROPS: Dict[str, Callable] = {
    "row_count": row_count,
    "column_count": column_count,
}
