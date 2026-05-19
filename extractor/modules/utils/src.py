"""Extractor source helpers — thin wrappers around storage access properties."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

from storage.workspace import Workspace


def _lookup_file_access_rows(workspace: Workspace, rel_path: str, *, require_db: bool = False):
    label_suffix = ":db" if require_db else ""
    access_expr = "coalesce(f._db_connect, f.db_connect)" if require_db else "coalesce(f._file_open, f.file_open)"
    rows = workspace.cypher(
        f"MATCH (f:file{label_suffix}) WHERE f.path = $path RETURN {access_expr} AS access",
        params={"path": rel_path},
    )
    if len(rows) == 1:
        return rows
    basename = os.path.basename(rel_path)
    if not basename:
        return []
    return workspace.cypher(
        f"MATCH (f:file{label_suffix}) WHERE f.name = $name RETURN {access_expr} AS access",
        params={"name": basename},
    )


def get_file_open(workspace: Workspace, rel_path: str):
    if os.path.isabs(rel_path):
        return None
    rows = _lookup_file_access_rows(workspace, rel_path)
    if len(rows) != 1:
        return None
    return rows[0].get("access")


def get_file_src(workspace: Workspace, rel_path: str):
    """Compatibility alias for older extractor modules."""
    return get_file_open(workspace, rel_path)


def file_exists(workspace: Workspace, rel_path: str) -> bool:
    if os.path.isabs(rel_path):
        return os.path.exists(rel_path)
    file_open = get_file_open(workspace, rel_path)
    path = getattr(file_open, "path", None)
    if path:
        return os.path.exists(path)
    return False


def get_file_path(workspace: Workspace, rel_path: str) -> Optional[str]:
    if os.path.isabs(rel_path):
        return rel_path if os.path.exists(rel_path) else None
    file_open = get_file_open(workspace, rel_path)
    path = getattr(file_open, "path", None)
    if path:
        return path
    return None


@contextmanager
def open_text_file(workspace: Workspace, rel_path: str, mode="r", **kwargs):
    file_open = get_file_open(workspace, rel_path)
    if callable(file_open):
        fh = file_open(mode, **kwargs)
        try:
            yield fh
        finally:
            fh.close()
        return
    path = get_file_path(workspace, rel_path)
    if not path:
        raise FileNotFoundError(rel_path)
    with open(path, mode, **kwargs) as fh:
        yield fh


@contextmanager
def open_sqlite_db(workspace: Workspace, rel_path: str, *, readonly: bool = True):
    db_connect = None
    if not os.path.isabs(rel_path):
        rows = _lookup_file_access_rows(workspace, rel_path, require_db=True)
        if len(rows) == 1:
            db_connect = rows[0].get("access")

    if callable(db_connect):
        conn = db_connect(readonly=readonly)
    else:
        db_path = getattr(db_connect, "db_path", None) or get_file_path(workspace, rel_path)
        if not db_path:
            raise FileNotFoundError(rel_path)
        if readonly:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
