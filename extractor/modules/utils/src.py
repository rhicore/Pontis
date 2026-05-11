"""Extractor source helpers — 薄封装 `cypher + n.src`。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

from storage.workspace import Workspace


def _lookup_file_src_rows(workspace: Workspace, rel_path: str, *, require_db: bool = False):
    label_suffix = ":db" if require_db else ""
    rows = workspace.cypher(
        f"MATCH (f:file{label_suffix}) WHERE f.path = $path RETURN f.src AS src",
        params={"path": rel_path},
    )
    if len(rows) == 1:
        return rows
    basename = os.path.basename(rel_path)
    if not basename:
        return []
    return workspace.cypher(
        f"MATCH (f:file{label_suffix}) WHERE f.name = $name RETURN f.src AS src",
        params={"name": basename},
    )


def get_file_src(workspace: Workspace, rel_path: str):
    if os.path.isabs(rel_path):
        return None
    rows = _lookup_file_src_rows(workspace, rel_path)
    if len(rows) != 1:
        return None
    return rows[0].get("src")


def file_exists(workspace: Workspace, rel_path: str) -> bool:
    if os.path.isabs(rel_path):
        return os.path.exists(rel_path)
    src = get_file_src(workspace, rel_path)
    if src and src.has("path"):
        return os.path.exists(src.get("path"))
    return False


def get_file_path(workspace: Workspace, rel_path: str) -> Optional[str]:
    if os.path.isabs(rel_path):
        return rel_path if os.path.exists(rel_path) else None
    src = get_file_src(workspace, rel_path)
    if src and src.has("path"):
        return src.get("path")
    return None


@contextmanager
def open_text_file(workspace: Workspace, rel_path: str, mode="r", **kwargs):
    src = get_file_src(workspace, rel_path)
    if src and src.has("open"):
        fh = src.get("open")(mode, **kwargs)
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
    src = None
    if not os.path.isabs(rel_path):
        rows = _lookup_file_src_rows(workspace, rel_path, require_db=True)
        if len(rows) == 1:
            src = rows[0].get("src")

    if src and src.has("db_connect"):
        conn = src.get("db_connect")(readonly=readonly)
    else:
        if src and src.has("path"):
            db_path = src.get("path")
        else:
            db_path = get_file_path(workspace, rel_path)
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
