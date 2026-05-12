"""FS module — 文件系统子图、src、匹配规则、虚属性注册。"""

from __future__ import annotations

import builtins
import csv
import json
import fnmatch
import os
from typing import Callable, Dict, Optional
import xml.etree.ElementTree as ET

from storage.enricher import enrich_meta
from storage.src import SrcHandle
from storage.stores.base import MatchQuery, MatchResult, StoreModule
from storage.stores.text import is_text_file
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


def csv_delimiter(project_path: str, file_rel_path: str, entity_path: str = "") -> str:
    return "\\t" if file_rel_path.lower().endswith(".tsv") else ","


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
    "csv": {
        **COMMON_FILE_PROPS,
        "delimiter": csv_delimiter,
    },
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
        self._light_meta_cache: Dict[str, tuple[tuple[int, int], dict]] = {}

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
            root.setdefault("labels", root.get("labels", []))
            nodes.append(root)

        self._scan_dirs()
        for rel_path, bare_name in self._dirs_cache.items():
            meta = self.get_virtual_meta(rel_path)
            if meta is None:
                continue
            node = {"name": bare_name, **meta}
            node.setdefault("path", rel_path)
            node.setdefault("labels", node.get("labels", []))
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
                node.setdefault("labels", node.get("labels", []))
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
        meta = enrich_meta(meta, self.store._project_path, key, "", store=self)
        meta.update({k: v for k, v in self._file_light_meta(key, meta).items() if k not in meta})
        return meta

    def get_virtual_neighbors(self, key: str):
        return self._dir_adjacent(key)

    def bind_src(self, node: dict):
        labels = set(node.get("labels", []) or [])
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
        labels = set(node.get("labels", []) or [])
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
        deduped = []
        for rel in results:
            deduped.append(rel)
        return [(rel, os.path.basename(rel), ["file"], "file") for rel in deduped]

    def _dir_meta(self, key: str) -> Optional[dict]:
        if not self.store.project_path:
            return None
        dir_path = self.store.project_path if key == "." else os.path.join(self.store.project_path, key)
        if not os.path.isdir(dir_path):
            return None
        return {"labels": ["dir"]}

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
            labels = db_utils.file_labels(name) or label_map.get(ext)
            if labels is None:
                labels = ["file", "text"] if is_text_file(os.path.basename(name)) else ["file"]
            return {"_inode": stat.st_ino, "labels": labels}
        except OSError:
            return None

    def _file_light_meta(self, rel_path: str, meta: dict) -> dict:
        labels = set(meta.get("labels", []) or [])
        if not labels.intersection({"csv", "json", "yaml", "xml", "toml", "hcl", "text"}):
            return {}
        full = self._source_path(rel_path)
        try:
            stat = os.stat(full)
        except OSError:
            return {}
        sig = (stat.st_mtime_ns, stat.st_size)
        cached = self._light_meta_cache.get(rel_path)
        if cached and cached[0] == sig:
            return dict(cached[1])

        result: dict = {}
        if "csv" in labels:
            result.update(self._csv_file_meta(rel_path, stat.st_size))
        if labels.intersection({"json", "yaml", "xml", "toml", "hcl"}):
            result.update(self._serialized_file_meta(rel_path, stat.st_size))
        if "text" in labels:
            result.update(self._text_file_meta(rel_path, stat.st_size))

        self._light_meta_cache[rel_path] = (sig, result)
        return dict(result)

    def _csv_file_meta(self, rel_path: str, file_size_bytes: int) -> dict:
        full = self._source_path(rel_path)
        delimiter = "\t" if rel_path.lower().endswith(".tsv") else ","
        result = {"delimiter": "\\t" if delimiter == "\t" else ","}
        try:
            with open(full, "r", encoding="utf-8", errors="ignore", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                headers = next(reader, None)
                result["column_count"] = len(headers or [])
                if file_size_bytes <= 5 * 1024 * 1024:
                    result["row_count"] = sum(1 for _ in reader)
        except Exception:
            return result
        return result

    def _serialized_file_meta(self, rel_path: str, file_size_bytes: int) -> dict:
        if file_size_bytes > 2 * 1024 * 1024:
            return {}
        full = self._source_path(rel_path)
        suffix = os.path.splitext(rel_path)[1].lower()
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            return {}
        result = {"line_count": len(content.splitlines()), "char_count": len(content)}

        if suffix in (".json", ".jsonl"):
            try:
                data = json.loads(content)
            except Exception:
                return {**result, "structure_type": "invalid_json"}
            return {**result, **self._top_structure_meta(data)}
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(content)
            except Exception:
                return {**result, "structure_type": "invalid_yaml"}
            return {**result, **self._top_structure_meta(data, mapping_name="mapping", sequence_name="sequence")}
        if suffix == ".xml":
            try:
                root = ET.fromstring(content)
            except Exception:
                return {**result, "structure_type": "invalid_xml"}
            root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
            child_tags = sorted({
                child.tag.split("}")[-1] if "}" in child.tag else child.tag
                for child in root
            })[:20]
            return {**result, "structure_type": "xml", "root_element": root_tag, "child_elements": child_tags}
        if suffix == ".toml":
            try:
                import tomllib
                with open(full, "rb") as f:
                    data = tomllib.load(f)
            except Exception:
                return {**result, "structure_type": "invalid_toml"}
            return {**result, **self._top_structure_meta(data, mapping_name="table")}
        if suffix == ".hcl":
            return {**result, "structure_type": "hcl"}
        return result

    @staticmethod
    def _top_structure_meta(data, mapping_name: str = "object", sequence_name: str = "array") -> dict:
        if isinstance(data, dict):
            return {
                "structure_type": mapping_name,
                "top_level_keys": list(data.keys())[:20],
                "key_count": len(data),
            }
        if isinstance(data, list):
            return {"structure_type": sequence_name, "array_length": len(data)}
        return {"structure_type": type(data).__name__}

    def _text_file_meta(self, rel_path: str, file_size_bytes: int) -> dict:
        if file_size_bytes > 2 * 1024 * 1024:
            return {}
        full = self._source_path(rel_path)
        encoding = self._detect_encoding(full)
        try:
            with open(full, "r", encoding=encoding, errors="ignore") as f:
                content = f.read()
        except OSError:
            return {}
        return {
            "encoding": encoding,
            "line_count": len(content.splitlines()),
            "char_count": len(content),
        }

    @staticmethod
    def _detect_encoding(path: str) -> str:
        try:
            import chardet
        except ImportError:
            return "utf-8"
        try:
            with open(path, "rb") as f:
                raw = f.read(10000)
            if not raw:
                return "utf-8"
            result = chardet.detect(raw)
            return result.get("encoding") or "utf-8"
        except Exception:
            return "utf-8"

    def _rebuild_inode_index(self):
        self._inode_index.clear()
        for _, raw in self.store._scan_entities():
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
                    children.append((matched_name, meta.get("name", entry), meta.get("labels", [])))
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
