"""Shared low-level graph writes for deterministic extractors."""
from __future__ import annotations

from typing import Any

from storage.workspace import Workspace


REVIEW_FIELDS = frozenset({"review_status", "brief", "detail"})


def refreshable_metadata(metadata: dict, *, preserve=REVIEW_FIELDS) -> dict:
    """Return extractor-owned fields while preserving later review content."""

    return {key: value for key, value in metadata.items() if key not in preserve}


def write_project_cypher(
    workspace: Workspace,
    query: str,
    params: dict | None = None,
) -> list[Any]:
    """Execute extractor-internal Cypher once per active project."""

    rows: list[Any] = []
    for project in workspace.active_projects:
        store = workspace._get_store(project)
        if store is None:
            continue
        scoped = {**dict(params or {}), "project": project}
        with store.execution_lock:
            rows.extend(store.execute_cypher(query, params=scoped))
    return rows
