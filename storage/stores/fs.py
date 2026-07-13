"""Filesystem source module.

This module only projects the physical filesystem shape:
- directories
- files
- labels derived from file suffixes
- file_open access properties

Content-aware projections such as text statistics, CSV columns, and database
schema belong to dedicated source modules.
"""

from __future__ import annotations

from datetime import datetime
import os
import re
from typing import Optional

from storage.stores.base import (
    CypherStatement,
    ModuleContext,
    StoreModule,
    cypher_label_clause,
)
from storage.stores.access import FileOpen


_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")


def _safe_suffix_label(suffix: str) -> str:
    label = suffix.lstrip(".").lower()
    label = _LABEL_RE.sub("_", label)
    if not label:
        return ""
    if label[0].isdigit():
        label = f"ext_{label}"
    return label


def _suffix_labels(path_or_name: str) -> list[str]:
    suffix = os.path.splitext(path_or_name)[1].lower()
    labels = ["file"]
    suffix_label = _safe_suffix_label(suffix)
    if suffix_label:
        labels.append(suffix_label)
    return labels


class FSModule(StoreModule):
    name = "fs"
    query_labels = {"dir", "file"}

    def __init__(self, ctx: ModuleContext):
        super().__init__(ctx)
        self._dirs_cache: dict[str, str] = {}
        self._suffix_label_cache: set[str] | None = None

    @property
    def project_path(self) -> str:
        return self.ctx.source.root

    def provides_source_anchor(self) -> bool:
        return (getattr(self.ctx.source_config, "type", "") or "").lower() == "fs"

    def should_materialize_for_query(self, parsed, raw_query: str = "") -> bool:
        if ".file_open" in raw_query or "._file_open" in raw_query:
            return True
        for node in getattr(parsed, "nodes", []) or []:
            labels = set(getattr(node, "labels", []) or [])
            if labels & self.query_labels:
                return True
            if labels & self._source_suffix_labels():
                return True
        return False

    def source_fingerprint(self) -> str | None:
        if not self.project_path:
            return None
        parts = []
        for root, dirs, files in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for name in dirs + files:
                rel = os.path.join(root, name) if root else name
                try:
                    stat = self.ctx.source.stat(rel)
                except OSError:
                    continue
                parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(sorted(parts))

    def cypher_statements(self) -> list[CypherStatement]:
        nodes = self.iter_virtual_nodes()
        edges = self.iter_virtual_edges(nodes)
        statements: list[CypherStatement] = []

        grouped: dict[tuple[str, ...], list[dict]] = {}
        for node in nodes:
            labels = tuple(node.get("labels", []) or [])
            props = {k: v for k, v in node.items() if k != "labels" and v is not None}
            grouped.setdefault(labels, []).append({
                "path": node.get("path"),
                "labels": list(labels),
                "props": props,
            })

        for labels, rows in grouped.items():
            statements.append(CypherStatement(
                query=(
                    "UNWIND $rows AS row "
                    "MERGE (n {path: row.path}) "
                    "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
                    "ON MATCH SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)) "
                    "SET n += row.props "
                    "REMOVE n.src, n.file_handle, n.dir_handle, n.file_open "
                    "WITH n, row.labels AS labels "
                    "SET n.labels = reduce(acc = [], label IN coalesce(n.labels, []) + labels | "
                    "CASE WHEN label IN acc THEN acc ELSE acc + label END) "
                    f"SET n{cypher_label_clause(list(labels))}"
                ),
                params={"rows": rows},
            ))

        if edges:
            statements.append(CypherStatement(
                query=(
                    "UNWIND $edges AS edge "
                    "MATCH (a {path: edge.a}) "
                    "MATCH (b {path: edge.b}) "
                    "MERGE (a)-[:RELATED_TO]->(b)"
                ),
                params={
                    "edges": [{"a": a, "b": b} for a, b in edges],
                },
            ))

        return statements

    def iter_virtual_nodes(self) -> list[dict]:
        nodes = []
        root_meta = self.get_virtual_meta(".")
        if root_meta is not None:
            nodes.append({"name": ".", **root_meta})

        self._scan_dirs()
        for rel_path, bare_name in self._dirs_cache.items():
            meta = self.get_virtual_meta(rel_path)
            if meta is not None:
                nodes.append({"name": bare_name, **meta})

        for root, dirs, files in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                rel = os.path.join(root, fname) if root else fname
                meta = self.get_virtual_meta(rel)
                if meta is not None:
                    nodes.append({"name": os.path.basename(rel), **meta})

        dedup: dict[str, dict] = {}
        for node in nodes:
            dedup[node.get("path", node.get("name", ""))] = node
        return list(dedup.values())

    def iter_virtual_edges(self, nodes: list[dict]) -> list[tuple[str, str]]:
        edges = []
        for node in nodes:
            rel_path = node.get("path", "")
            if not rel_path or rel_path == ".":
                continue
            parent = os.path.dirname(rel_path) or "."
            edges.append((parent, rel_path))
        return edges

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        if not self.project_path:
            return None
        full = self.ctx.source.absolute_path(key)
        if os.path.isdir(full):
            meta = self._dir_meta(key)
        elif os.path.isfile(full):
            meta = self._file_meta(key)
        else:
            return None

        meta.setdefault("path", key)
        label_set = set(meta.get("labels", []) or [])
        if "file" in label_set:
            meta.setdefault("_file_open", self.pointer("open", key))
        return meta

    def bind_access(self, kind: str, node: dict):
        labels = set(node.get("labels", []) or [])
        rel_path = node.get("path") or node.get("name", "")
        if not rel_path:
            return None
        abs_path = self.ctx.source.absolute_path(rel_path)

        if kind == "open" and "file" in labels and os.path.isfile(abs_path):
            return FileOpen(abs_path)

        return None

    def resolve_pointer(self, kind: str, payload: str, *, node: dict | None = None):
        if kind != "open":
            return None
        meta = self.get_virtual_meta(payload) or {}
        src_node = dict(node or {})
        src_node.setdefault("path", payload)
        src_node.setdefault("name", os.path.basename(payload) or payload)
        src_node.setdefault("labels", meta.get("labels", []))
        return self.bind_access(kind, src_node)

    def _scan_dirs(self):
        if self._dirs_cache or not self.project_path:
            return
        for root, dirs, _ in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for dirname in dirs:
                rel = os.path.join(root, dirname) if root else dirname
                self._dirs_cache[rel] = os.path.basename(rel)

    def _source_suffix_labels(self) -> set[str]:
        if self._suffix_label_cache is not None:
            return self._suffix_label_cache
        labels: set[str] = set()
        if not self.project_path:
            self._suffix_label_cache = labels
            return labels
        for root, dirs, files in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                label = _safe_suffix_label(os.path.splitext(fname)[1].lower())
                if label:
                    labels.add(label)
        self._suffix_label_cache = labels
        return labels

    def _dir_meta(self, key: str) -> dict | None:
        full = self.ctx.source.absolute_path(key)
        if not os.path.isdir(full):
            return None
        entries = self._visible_entries(full)
        return {
            "_source_anchor": bool(key == "." and self.provides_source_anchor()),
            "labels": ["dir"],
            "child_count": len(entries),
            "file_count": sum(1 for entry in entries if os.path.isfile(os.path.join(full, entry))),
            "subdir_count": sum(1 for entry in entries if os.path.isdir(os.path.join(full, entry))),
        }

    def _file_meta(self, rel_path: str) -> dict | None:
        full = self.ctx.source.absolute_path(rel_path)
        if not os.path.isfile(full):
            return None
        try:
            stat = os.stat(full)
        except OSError:
            return None
        return {
            "_inode": stat.st_ino,
            "labels": _suffix_labels(rel_path),
            "file_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

    @staticmethod
    def _visible_entries(path: str) -> list[str]:
        try:
            return [entry for entry in os.listdir(path) if not entry.startswith(".")]
        except OSError:
            return []


__all__ = ["FSModule"]
