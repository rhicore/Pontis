"""FS module — 文件系统子图、src、匹配规则、虚属性注册。"""

from __future__ import annotations

import builtins
import fnmatch
import os
from typing import Callable, Dict, Optional

from storage.enricher import enrich_meta
from storage.src import SrcHandle
from storage.stores.base import MatchQuery, MatchResult, StoreModule
from storage.stores.utils import db as db_utils


def file_size(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    full = os.path.join(project_path, file_rel_path)
    try:
        return os.path.getsize(full)
    except OSError:
        return None


def modified_at(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[str]:
    from datetime import datetime
    full = os.path.join(project_path, file_rel_path)
    try:
        return datetime.fromtimestamp(os.path.getmtime(full)).isoformat()
    except OSError:
        return None


def child_count(project_path: str, file_rel_path: str, entity_path: str = "") -> int:
    full = os.path.join(project_path, file_rel_path)
    return len([e for e in os.listdir(full) if not e.startswith('.')])


def file_count(project_path: str, file_rel_path: str, entity_path: str = "") -> int:
    full = os.path.join(project_path, file_rel_path)
    return sum(1 for e in os.listdir(full)
               if not e.startswith('.') and os.path.isfile(os.path.join(full, e)))


def subdir_count(project_path: str, file_rel_path: str, entity_path: str = "") -> int:
    full = os.path.join(project_path, file_rel_path)
    return sum(1 for e in os.listdir(full)
               if not e.startswith('.') and os.path.isdir(os.path.join(full, e)))


COMMON_FILE_PROPS: Dict[str, Callable] = {
    "file_size": file_size,
    "modified_at": modified_at,
}

DIR_PROPS: Dict[str, Callable] = {
    "child_count": child_count,
    "file_count": file_count,
    "subdir_count": subdir_count,
}

PROP_REGISTRY: Dict[str, Dict[str, Callable]] = {
    "db": {
        **COMMON_FILE_PROPS,
        "table_count": db_utils.table_count,
        "view_count": db_utils.view_count,
        "index_count": db_utils.index_count,
    },
    "csv": dict(COMMON_FILE_PROPS),
    "json": dict(COMMON_FILE_PROPS),
    "text": dict(COMMON_FILE_PROPS),
    "yaml": dict(COMMON_FILE_PROPS),
    "xml": dict(COMMON_FILE_PROPS),
    "toml": dict(COMMON_FILE_PROPS),
    "hcl": dict(COMMON_FILE_PROPS),
    "file": dict(COMMON_FILE_PROPS),
    "table": {
        "row_count": db_utils.row_count,
        "column_count": db_utils.column_count,
    },
    "view": {
        "row_count": db_utils.row_count,
        "column_count": db_utils.column_count,
    },
}


class FSModule(StoreModule):
    name = "fs"
    prop_registry = PROP_REGISTRY
    dir_props = DIR_PROPS
    common_file_props = COMMON_FILE_PROPS

    def __init__(self, store):
        self.store = store
        self._dirs_cache: Dict[str, str] = {}
        self._inode_index: Dict[int, str] = {}

    def discover_virtual(self, pattern: str, label: str = None):
        results = []
        if label is None or label == "dir":
            results.extend(self._discover_dirs(pattern))
        if label is None or label != "dir":
            results.extend(self._discover_files(pattern))
        return results

    def iter_virtual_nodes(self) -> list[dict]:
        nodes = []
        root_meta = self.get_virtual_meta(".")
        if root_meta is not None:
            root = {"name": ".", **root_meta}
            root.setdefault("path", ".")
            root.setdefault("labels", root.pop("_labels", []))
            nodes.append(root)

        self._scan_dirs()
        for rel_path, bare_name in self._dirs_cache.items():
            meta = self.get_virtual_meta(rel_path)
            if meta is None:
                continue
            node = {"name": bare_name, **meta}
            node.setdefault("path", rel_path)
            node.setdefault("labels", node.pop("_labels", []))
            nodes.append(node)

        for root, dirs, files in os.walk(self.store._project_path):
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for f in files:
                fp = os.path.join(root, f)
                if self._is_backend_file(fp):
                    continue
                rel = os.path.relpath(fp, self.store._project_path)
                meta = self.get_virtual_meta(rel)
                if meta is None:
                    continue
                node = {"name": os.path.basename(rel), **meta}
                node.setdefault("path", rel)
                node.setdefault("labels", node.pop("_labels", []))
                nodes.append(node)

        dedup = {}
        for n in nodes:
            dedup[n.get("path", n.get("name", ""))] = n
        return list(dedup.values())

    def iter_virtual_edges(self, nodes: list[dict]) -> list[tuple[str, str]]:
        edges = []
        for n in nodes:
            rel_path = n.get("path", "")
            if not rel_path or rel_path == ".":
                continue
            parent = os.path.dirname(rel_path) or "."
            edges.append((parent, rel_path))
        return edges

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        full = os.path.join(self.store._project_path, key)
        if os.path.isdir(full):
            meta = self._dir_meta(key)
        elif os.path.isfile(full):
            meta = self._file_meta(key)
        else:
            meta = None
        if meta is None:
            return None
        meta = dict(meta)
        meta.setdefault("path", key)
        return enrich_meta(meta, self.store._project_path, key, "", store=self)

    def get_virtual_neighbors(self, key: str):
        return self._dir_adjacent(key)

    def bind_src(self, node: dict):
        labels = set(node.get("labels", []) or node.get("_labels", []) or [])
        rel_path = node.get("path") or node.get("name", "")
        if not rel_path:
            return None
        abs_path = self._source_path(rel_path)

        if "dir" in labels and os.path.isdir(abs_path):
            return SrcHandle(node, {"path": abs_path}, "dir")

        if "file" in labels and os.path.isfile(abs_path):
            ports = {
                "path": abs_path,
                "open": lambda *args, **kwargs: builtins.open(abs_path, *args, **kwargs),
            }
            if "db" in labels:
                ports["db_connect"] = lambda *args, **kwargs: db_utils.connect_sqlite(abs_path, *args, **kwargs)
                return SrcHandle(node, ports, "sqlite")
            return SrcHandle(node, ports, "file")

        return None

    def match_query(self, node: dict) -> MatchQuery | None:
        labels = set(node.get("labels", []) or node.get("_labels", []) or [])
        if "file" not in labels and "dir" not in labels:
            return None

        path = node.get("path")
        if not path:
            return None
        return MatchQuery(
            query="MATCH (n) WHERE n.path = $path RETURN n",
            params={"path": path},
            var="n",
        )

    def meta_fallback(self, ref: str, include_props=None, _visiting=None) -> dict | None:
        if not self.store.project_path:
            return None
        physical = self._source_path(ref)
        if not os.path.exists(physical):
            return None
        result = enrich_meta({}, self.store.project_path, ref, "",
                             include_props=include_props, store=self)
        return result if result else None

    def _is_backend_file(self, fp: str) -> bool:
        if not self.store._backend_db_path:
            return False
        absp = os.path.abspath(fp)
        db_base = os.path.abspath(self.store._backend_db_path)
        return absp == db_base or absp.startswith(db_base + "-")

    def _source_path(self, rel_path: str) -> str:
        return os.path.join(self.store.project_path, rel_path)

    def _scan_dirs(self):
        if self._dirs_cache or not self.store.project_path:
            return
        for root, dirs, _ in os.walk(self.store.project_path):
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for d in dirs:
                full = os.path.join(root, d)
                rel = os.path.relpath(full, self.store.project_path)
                self._dirs_cache[rel] = os.path.basename(rel)

    def _discover_dirs(self, pattern: str) -> list[tuple[str, str, list[str], str]]:
        self._scan_dirs()
        results = []
        for rel_path, bare_name in self._dirs_cache.items():
            if fnmatch.fnmatch(bare_name, pattern):
                results.append((rel_path, bare_name, ["dir"], "dir"))
        if fnmatch.fnmatch(".", pattern):
            results.append((".", ".", ["dir"], "dir"))
        return results

    def _discover_files(self, pattern: str) -> list[tuple[str, str, list[str], str]]:
        results = []
        project_path = self.store.project_path
        if not project_path:
            return results
        if "**" in pattern:
            base = os.path.join(project_path, pattern.split("**")[0] or "")
            if not os.path.isdir(base):
                base = project_path
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != ".pontis"]
                for f in files:
                    fp = os.path.join(root, f)
                    if self._is_backend_file(fp):
                        continue
                    rel = os.path.relpath(fp, project_path)
                    if fnmatch.fnmatch(rel, pattern.replace("\\", "/")):
                        results.append(rel)
        else:
            full = os.path.join(project_path, pattern)
            dir_part = os.path.dirname(full)
            pat_part = os.path.basename(full)
            if os.path.isdir(dir_part):
                for f in os.listdir(dir_part):
                    if fnmatch.fnmatch(f, pat_part):
                        fp = os.path.join(dir_part, f)
                        if os.path.isfile(fp) and not self._is_backend_file(fp):
                            rel = os.path.relpath(fp, project_path)
                            if ".pontis" not in rel.split(os.sep):
                                results.append(rel)
        self.store._ensure_index()
        deduped = []
        for rel in results:
            bare = os.path.basename(rel)
            ent_ids = self.store._name_to_ids(bare)
            if ent_ids:
                found = False
                for eid in ent_ids:
                    cached = self.store._meta_cache.get(eid, {})
                    if cached.get("path", bare) == rel:
                        found = True
                        break
                    raw = self.store._read_entity_meta(eid)
                    if raw and raw.get("path", bare) == rel:
                        found = True
                        break
                if found:
                    continue
            deduped.append(rel)
        return [(rel, os.path.basename(rel), ["file"], "file") for rel in deduped]

    def _dir_meta(self, key: str) -> Optional[dict]:
        if not self.store.project_path:
            return None
        dir_path = self.store.project_path if key == "." else os.path.join(self.store.project_path, key)
        if not os.path.isdir(dir_path):
            return None
        return {"_labels": ["dir"]}

    def _file_meta(self, name: str) -> Optional[dict]:
        if not self.store.project_path:
            return None
        physical = os.path.join(self.store.project_path, name)
        if not os.path.exists(physical):
            return None
        try:
            stat = os.stat(physical)
            ext = os.path.splitext(name)[1].lower()
            label_map = {
                ".csv": ["file", "csv"], ".tsv": ["file", "csv"],
                ".json": ["file", "json"], ".jsonl": ["file", "json"],
                ".yaml": ["file", "yaml"], ".yml": ["file", "yaml"],
                ".xml": ["file", "xml"],
                ".toml": ["file", "toml"],
                ".hcl": ["file", "hcl"],
            }
            return {"_inode": stat.st_ino, "_labels": db_utils.file_labels(name) or label_map.get(ext, ["file"])}
        except OSError:
            return None

    def _rebuild_inode_index(self):
        self._inode_index.clear()
        for _eid, raw in self.store._scan_entities():
            inode = raw.get("_inode")
            if inode is not None:
                self._inode_index[inode] = raw.get("name", "")

    def _dir_adjacent(self, key: str) -> list[tuple[str, str, list]]:
        if not self.store.project_path:
            return []
        dir_path = self.store.project_path if key == "." else os.path.join(self.store.project_path, key)
        if not os.path.isdir(dir_path):
            return []
        try:
            entries = os.listdir(dir_path)
        except OSError:
            return []
        self.store._ensure_index()
        self._rebuild_inode_index()
        children = []
        for entry in entries:
            if entry.startswith("."):
                continue
            full = os.path.join(dir_path, entry)
            if self._is_backend_file(full):
                continue
            if os.path.isdir(full):
                child_rel = os.path.relpath(full, self.store.project_path)
                children.append((child_rel, os.path.basename(child_rel), ["dir"]))
            elif os.path.isfile(full):
                try:
                    inode = os.stat(full).st_ino
                except OSError:
                    continue
                matched_name = self._inode_index.get(inode)
                if matched_name:
                    meta = self.store._get_meta(matched_name) or {}
                    children.append((matched_name, meta.get("name", entry), meta.get("_labels", [])))
                else:
                    child_rel = os.path.relpath(full, self.store.project_path)
                    children.append((child_rel, entry, ["file"]))
        return children


__all__ = [
    "FSModule",
    "MatchResult",
    "PROP_REGISTRY",
    "DIR_PROPS",
    "COMMON_FILE_PROPS",
]
