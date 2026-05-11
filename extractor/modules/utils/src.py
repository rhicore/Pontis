"""Extractor source helpers — 薄封装 `cypher + n.src`。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

from storage.workspace import Workspace


def get_file_src(workspace: Workspace, rel_path: str):
    if os.path.isabs(rel_path):
        return None
    rows = workspace.cypher(
        "MATCH (f:file) WHERE f.name = $file OR f.path = $file RETURN f.src AS src",
        params={"file": rel_path},
    )
    if len(rows) != 1:
        return None
    return rows[0].get("src")


def file_exists(workspace: Workspace, rel_path: str) -> bool:
    if os.path.isabs(rel_path):
        return os.path.exists(rel_path)
    src = get_file_src(workspace, rel_path)
    if src and src.has("path"):
        return os.path.exists(src.get("path"))
    return os.path.exists(os.path.join(workspace.project_path, rel_path))


def get_file_path(workspace: Workspace, rel_path: str) -> Optional[str]:
    if os.path.isabs(rel_path):
        return rel_path if os.path.exists(rel_path) else None
    src = get_file_src(workspace, rel_path)
    if src and src.has("path"):
        return src.get("path")
    path = os.path.join(workspace.project_path, rel_path)
    return path if os.path.exists(path) else None


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
        rows = workspace.cypher(
            "MATCH (f:file:db) WHERE f.name = $file OR f.path = $file RETURN f.src AS src",
            params={"file": rel_path},
        )
        if len(rows) == 1:
            src = rows[0].get("src")

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
