"""Workspace-aware access helpers for tools.

Tools should prefer storage-owned handles over direct filesystem access. This
module centralizes the few cases where a tool needs to inspect workspace source
configuration or resolve a file handle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Iterable, Optional


@dataclass(frozen=True)
class OpenFileSource:
    path: str
    name: str
    open_file: object
    labels: tuple[str, ...] = ()
    line_count: Optional[int] = None
    char_count: Optional[int] = None
    file_size: Optional[int] = None


def default_project(workspace) -> str:
    active = list(getattr(workspace, "active_projects", []) or [])
    if len(active) == 1:
        return active[0]
    return active[0] if active else ""


def workspace_allows_direct_fs(workspace, project: str | None = None) -> bool:
    """Return whether tools may touch the local filesystem directly.

    Direct access is only allowed for local filesystem sources. Non-fs sources
    must go through storage handles.
    """
    project = project or default_project(workspace)
    if not project:
        return False
    cfg = getattr(workspace, "config", None)
    entry = getattr(cfg, "projects", {}).get(project) if cfg else None
    source = getattr(entry, "source", None)
    if not source or getattr(source, "type", "") != "fs":
        return False
    root = getattr(workspace, "project_path", "") or ""
    return bool(root and os.path.isdir(root))


def normalize_rel_path(path: str = "", current_cwd: str = "") -> str:
    text = (path or "").strip()
    cwd = (current_cwd or "").strip()
    if not text:
        text = "."
    if os.path.isabs(text) or not cwd or text == ".":
        return os.path.normpath(text)
    return os.path.normpath(os.path.join(cwd, text))


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project or None, local_ref


def _strip_display_labels(ref: str) -> str:
    """Convert a displayed file ref like `notes/a.md:file:text` to its path part."""
    if ":" not in ref:
        return ref
    parts = [p for p in ref.split("/") if p]
    if not parts:
        return ref
    last = parts[-1]
    if ":" not in last:
        return ref
    parts[-1] = last.split(":", 1)[0]
    return "/".join(parts)


def _row_to_source(row: dict) -> Optional[OpenFileSource]:
    open_file = row.get("open_file")
    if open_file is None or not callable(open_file):
        return None
    path = row.get("path") or getattr(open_file, "path", "")
    if not path:
        return None
    labels = row.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    return OpenFileSource(
        path=path,
        name=row.get("name") or os.path.basename(path),
        open_file=open_file,
        labels=tuple(labels),
        line_count=row.get("line_count"),
        char_count=row.get("char_count"),
        file_size=row.get("file_size"),
    )


def _sources_from_rows(rows: Iterable[dict]) -> list[OpenFileSource]:
    sources = []
    seen = set()
    for row in rows:
        src = _row_to_source(row)
        if src is None or src.path in seen:
            continue
        sources.append(src)
        seen.add(src.path)
    return sources


def _sources_from_ref_pattern(workspace, ref: str, project: str | None) -> list[OpenFileSource]:
    """Resolve a graph ref pattern to file open handles."""
    from tool.utils.ref_match import _apply_post_filters, _build_cypher, parse_urn

    _, segments = parse_urn(ref)
    cypher, post_filters = _build_cypher(segments)
    rows = workspace.cypher(cypher, project=project) if project else workspace.cypher(cypher)
    if post_filters:
        rows = _apply_post_filters(rows, post_filters)

    projected = []
    for row in rows:
        node = None
        for value in reversed(list(row.values())):
            if isinstance(value, dict):
                node = value
                break
        if not node:
            continue
        labels = node.get("labels") or []
        if "file" not in labels:
            continue
        projected.append({
            "path": node.get("path"),
            "name": node.get("name"),
            "labels": labels,
            "line_count": node.get("line_count"),
            "char_count": node.get("char_count"),
            "file_size": node.get("file_size"),
            "open_file": node.get("_file_open") or node.get("file_open"),
        })
    return _sources_from_rows(projected)


def resolve_file_sources(
    workspace,
    path: str = "",
    *,
    labels: tuple[str, ...] = (),
    current_cwd: str = "",
    allow_directory: bool = False,
    file_pattern: str | None = None,
) -> list[OpenFileSource]:
    """Resolve file nodes to storage-owned FileOpen handles."""
    selector = (path or "").strip()
    project, graph_selector = _split_project_ref(selector)
    # Keep the graph selector intact for wildcard refs.  In particular,
    # `*:file:text` must reach ref_match with its labels; stripping the display
    # suffix first silently widens it to `*` and lets binary files through.
    path_selector = _strip_display_labels(graph_selector)
    rel_path = normalize_rel_path(path_selector, current_cwd)
    label_clause = "".join(f":{label}" for label in labels)
    file_patterns = [p.strip() for p in (file_pattern or "").split(",") if p.strip()]

    def filter_file_patterns(items: list[OpenFileSource]) -> list[OpenFileSource]:
        if not file_patterns:
            return items
        return [
            item for item in items
            if any(fnmatch(item.path, pat) or fnmatch(item.name, pat) for pat in file_patterns)
        ]

    has_wildcards = any(c in graph_selector for c in "*?[]")
    if selector and has_wildcards:
        sources = filter_file_patterns(_sources_from_ref_pattern(workspace, graph_selector, project))
        if labels:
            wanted = set(labels)
            sources = [source for source in sources if wanted.issubset(set(source.labels))]
        if sources:
            return sorted(sources, key=lambda s: s.path)

    queries: list[tuple[str, dict]] = []
    if rel_path in ("", "."):
        queries.append((
            f"MATCH (n:file{label_clause}) "
            "RETURN n.path AS path, n.name AS name, n.labels AS labels, "
            "n.line_count AS line_count, n.char_count AS char_count, n.file_size AS file_size, "
            "coalesce(n._file_open, n.file_open) AS open_file",
            {},
        ))
    else:
        queries.append((
            f"MATCH (n:file{label_clause}) "
            "WHERE n._ref = $ref OR n.ref = $ref "
            "RETURN n.path AS path, n.name AS name, n.labels AS labels, "
            "n.line_count AS line_count, n.char_count AS char_count, n.file_size AS file_size, "
            "coalesce(n._file_open, n.file_open) AS open_file",
            {"ref": selector},
        ))
        queries.append((
            f"MATCH (n:file{label_clause} {{path: $path}}) "
            "RETURN n.path AS path, n.name AS name, n.labels AS labels, "
            "n.line_count AS line_count, n.char_count AS char_count, n.file_size AS file_size, "
            "coalesce(n._file_open, n.file_open) AS open_file",
            {"path": rel_path},
        ))
        queries.append((
            f"MATCH (n:file{label_clause} {{name: $name}}) "
            "RETURN n.path AS path, n.name AS name, n.labels AS labels, "
            "n.line_count AS line_count, n.char_count AS char_count, n.file_size AS file_size, "
            "coalesce(n._file_open, n.file_open) AS open_file",
            {"name": os.path.basename(rel_path)},
        ))
        if allow_directory:
            prefix = rel_path.rstrip("/") + "/"
            queries.append((
                f"MATCH (n:file{label_clause}) WHERE n.path STARTS WITH $prefix "
                "RETURN n.path AS path, n.name AS name, n.labels AS labels, "
                "n.line_count AS line_count, n.char_count AS char_count, n.file_size AS file_size, "
                "coalesce(n._file_open, n.file_open) AS open_file",
                {"prefix": prefix},
            ))

    for query, params in queries:
        rows = workspace.cypher(query, params=params, project=project) if project else workspace.cypher(query, params=params)
        sources = filter_file_patterns(_sources_from_rows(rows))
        if sources:
            return sorted(sources, key=lambda s: s.path)
    return []


def physical_path_for_open_file(open_file) -> str:
    return getattr(open_file, "path", "") or ""
