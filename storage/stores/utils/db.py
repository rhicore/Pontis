"""DB helpers reused by source modules.

当前先只提供 SQLite/file-db 相关能力。
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

DB_FILE_LABELS = {
    ".db": ["file", "db"],
    ".sqlite": ["file", "db"],
    ".sqlite3": ["file", "db"],
    ".duckdb": ["file", "db"],
}


def file_labels(path_or_name: str) -> list[str] | None:
    ext = os.path.splitext(path_or_name)[1].lower()
    return DB_FILE_LABELS.get(ext)


def connect_sqlite(path: str, *args, readonly: bool = False,
                   immutable: bool = False, **kwargs):
    if readonly or immutable:
        qs = ["mode=ro"]
        if immutable:
            qs.append("immutable=1")
        path = f"file:{path}?{'&'.join(qs)}"
        kwargs["uri"] = True
    return sqlite3.connect(path, *args, **kwargs)


def table_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        n = cur.fetchone()[0]
        conn.close()
        return n
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
        n = cur.fetchone()[0]
        conn.close()
        return n
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
        n = cur.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return None


def row_count(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    table_name = entity_path.split("/")[-1] if "/" in entity_path else entity_path
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        n = cur.fetchone()[0]
        conn.close()
        return n
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
        n = len(cur.fetchall())
        conn.close()
        return n
    except Exception:
        return None
