"""数据库文件虚属性 — table_count, view_count, index_count"""
import os
import sqlite3
from typing import Dict, Callable, Optional

from .common import COMMON_FILE_PROPS


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


# .db 文件的虚属性注册
PROPS: Dict[str, Dict[str, Callable]] = {
    ".db": {
        **COMMON_FILE_PROPS,
        "table_count": table_count,
        "view_count": view_count,
        "index_count": index_count,
    },
    ".sqlite": {
        **COMMON_FILE_PROPS,
        "table_count": table_count,
        "view_count": view_count,
        "index_count": index_count,
    },
}
