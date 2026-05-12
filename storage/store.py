"""Store — 唯一主图实现。"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Dict, Iterator, List, Optional, Tuple

from storage.backends import GraphBackend
from storage.config import SourceConfig
logger = logging.getLogger(__name__)

_BASE_INTERNAL_FIELDS = set()
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: Dict[str, threading.RLock] = {}


def _gen_id() -> str:
    return f"ent_{uuid.uuid4().hex[:8]}"


def _lock_key_for_store(project_path: str, backend_db_path: str | None) -> str:
    if backend_db_path:
        return f"db:{os.path.abspath(backend_db_path)}"
    if project_path:
        return f"project:{os.path.abspath(project_path)}"
    return "project:<anonymous>"


def _get_store_lock(project_path: str, backend_db_path: str | None) -> threading.RLock:
    key = _lock_key_for_store(project_path, backend_db_path)
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock


class Store:
    """唯一主图实现。

    - backend 负责物理持久化
    - store 负责图语义、索引、模块调度
    """

    def __init__(self, source_config: SourceConfig, backend: GraphBackend):
        self._source_config = source_config
        self._backend = backend
        self._backend.connect()

        self._project_path = os.path.abspath(source_config.path) if source_config.path else ""
        self._pontis_root = os.path.join(self._project_path, ".pontis") if self._project_path else ""
        self._backend_db_path = getattr(backend, "_db_path", None)
        self._project_name = ""
        self._execution_lock = _get_store_lock(self._project_path, self._backend_db_path)

        self._meta_cache: Dict[str, Optional[dict]] = {}
        self._id_index: Dict[str, dict] = {}
        self._adjacent: Dict[str, set] = {}
        self._cross_adjacent: Dict[str, List[dict]] = {}

        self._read_timestamps: Dict[str, float] = {}
        self._modules: list = []

        self._last_version: int = -1
        self._index_built = False

    # ==================== properties ====================

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def pontis_exists(self) -> bool:
        if self._pontis_root:
            return os.path.exists(self._pontis_root)
        return True

    @property
    def index_root(self) -> str:
        return os.path.join(self._pontis_root, "_index") if self._pontis_root else ""

    @property
    def internal_fields(self) -> set:
        return set(_BASE_INTERNAL_FIELDS)

    @property
    def modules(self) -> list:
        return list(self._modules)

    def add_module(self, module):
        self._modules.append(module)

    @property
    def execution_lock(self):
        return self._execution_lock

    # ==================== backend delegates ====================

    def _scan_entities(self) -> List[Tuple[str, dict]]:
        return self._backend.scan_nodes()

    def _read_entity_meta(self, ent_id: str) -> Optional[dict]:
        return self._backend.read_node(ent_id)

    def _write_entity_meta(self, ent_id: str, data: dict):
        self._backend.write_node(ent_id, data)

    def _delete_entity_storage(self, ent_id: str):
        self._backend.delete_node(ent_id)
        self._backend.remove_edges_for(ent_id)

    def _read_edges_storage(self) -> List[dict]:
        return self._backend.read_edges()

    def _write_edges_storage(self, edges: List[dict]):
        self._backend.write_edges(edges)

    def _read_version(self) -> int:
        return self._backend.read_version()

    def _bump_version(self) -> int:
        return self._backend.bump_version()

    def _persist_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        self._backend.add_cross_edge(from_id, to_project, to_entity_id)

    def _delete_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        self._backend.remove_cross_edge(from_id, to_project, to_entity_id)

    def _delete_cross_edges_for(self, from_id: str):
        self._backend.remove_cross_edges_for(from_id)

    def _update_cross_edge_stale(self, from_id: str, to_project: str, to_entity_id: str, *, stale: bool):
        self._backend.set_cross_edge_stale(from_id, to_project, to_entity_id, stale=stale)

    def _load_cross_edges(self) -> Dict[str, List[dict]]:
        return self._backend.read_cross_edges()

    # ==================== src / modules ====================

    def bind_src(self, node: dict):
        eid = node.get("id") if isinstance(node, dict) else None
        if eid and eid in self._id_index:
            node = dict(self._id_index[eid])
            node["id"] = eid
        for mod in self._modules:
            src = mod.bind_src(node)
            if src is not None:
                return src
        return None

    def discover_virtual(self, pattern: str, label: str = None) -> list:
        results = []
        for mod in self._modules:
            results.extend(mod.discover_virtual(pattern, label))
        return results

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        merged = None
        labels = set()
        for mod in self._modules:
            meta = mod.get_virtual_meta(key)
            if not meta:
                continue
            meta = dict(meta)
            if merged is None:
                merged = meta
            else:
                labels |= set(merged.get("labels", []))
                merged.update(meta)
            labels |= set(meta.get("labels", []))
        if merged is not None and labels:
            merged["labels"] = sorted(labels)
        return merged

    def get_virtual_neighbors(self, key: str) -> list:
        results = []
        for mod in self._modules:
            results.extend(mod.get_virtual_neighbors(key))
        return results

    # ==================== indexing ====================

    def _mark_dirty(self):
        self._last_version = self._bump_version()

    def _ensure_index(self):
        current = self._read_version()
        if self._index_built and current == self._last_version:
            return
        self._build_index()
        self._last_version = current

    def _build_index(self):
        from storage.labels import normalize_labels
        self._id_index.clear()
        self._adjacent.clear()

        for ent_id, raw in self._scan_entities():
            raw["id"] = ent_id
            if "labels" in raw:
                raw["labels"] = normalize_labels(raw["labels"])
            raw = self._sanitize_meta(raw)
            self._meta_cache[ent_id] = raw
            self._register_node(ent_id, raw)

        for e in self._read_edges_storage():
            nodes = e.get("nodes", [])
            for nid in nodes:
                for other in nodes:
                    if other != nid:
                        self._adjacent.setdefault(nid, set()).add(other)

        self._index_built = True
        self._cross_adjacent = self._load_cross_edges()

    def _register_node(self, ent_id: str, props: dict):
        self._id_index[ent_id] = props

    def _unregister_node(self, ent_id: str):
        self._id_index.pop(ent_id, None)

    def _sanitize_meta(self, data: Optional[dict], labels: Optional[List[str]] = None) -> dict:
        result = dict(data or {})
        if labels is not None:
            result["labels"] = list(labels)
        elif "labels" not in result:
            result["labels"] = []
        return result

    def _resolve_to_id(self, eid: str) -> Optional[str]:
        if not self._index_built:
            self._ensure_index()
        if isinstance(eid, str) and eid in self._id_index:
            return eid
        return None

    def _get_labels_by_id(self, ent_id: str) -> List[str]:
        props = self._id_index.get(ent_id)
        return props.get("labels", []) if props else []

    # ==================== meta ====================

    def _get_meta(self, ref: str, include_props: Optional[List[str]] = None, *, _visiting: Optional[set] = None) -> Optional[dict]:
        if _visiting is not None and ref in _visiting:
            return self._get_stored_meta(ref)
        _visiting = _visiting or set()
        _visiting.add(ref)
        try:
            return self._get_meta_internal(ref, include_props, _visiting)
        finally:
            _visiting.discard(ref)

    def _get_stored_meta(self, ref: str) -> Optional[dict]:
        ent_id = self._resolve_to_id(ref)
        if ent_id is None:
            return None
        if ent_id in self._meta_cache:
            cached = self._meta_cache[ent_id]
            if cached is None:
                return None
            return {k: v for k, v in cached.items() if not k.startswith("_") and k not in self.internal_fields}
        raw = self._read_entity_meta(ent_id)
        if raw is None:
            self._meta_cache[ent_id] = None
            return None
        self._meta_cache[ent_id] = dict(raw)
        return {k: v for k, v in raw.items() if not k.startswith("_") and k not in self.internal_fields}

    def _get_meta_internal(self, ref: str, include_props: Optional[List[str]], _visiting: set) -> Optional[dict]:
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if ent_id is not None:
            if ent_id in self._meta_cache:
                cached = self._meta_cache[ent_id]
                if cached is None:
                    return None
                result = dict(cached)
            else:
                raw = self._read_entity_meta(ent_id)
                if raw is None:
                    self._meta_cache[ent_id] = None
                    return None
                result = dict(raw)
                self._meta_cache[ent_id] = dict(raw)
                self._on_meta_read(ent_id)
            result = {k: v for k, v in result.items() if not k.startswith("_") and k not in self.internal_fields}
            return result
        return self._get_meta_fallback(ref, include_props, _visiting)

    def _read_raw(self, ent_id: str) -> Optional[dict]:
        return self._read_entity_meta(ent_id)

    def _get_meta_fallback(self, ref: str, include_props=None, _visiting=None) -> Optional[dict]:
        for mod in self._modules:
            try:
                result = mod.meta_fallback(ref, include_props=include_props, _visiting=_visiting)
            except Exception:
                result = None
            if result:
                return result
        return None

    # ==================== create/update/delete ====================

    def _set_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return ""
        existing = self._read_raw(ent_id) or {}
        existing.update(data)
        existing = self._sanitize_meta(existing)
        self._register_node(ent_id, existing)
        self._write_entity_meta(ent_id, existing)
        self._meta_cache[ent_id] = dict(existing)
        self._on_meta_read(ent_id)
        self._mark_dirty()
        return ent_id

    def _put_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return ""
        data = self._sanitize_meta(data)
        data["id"] = ent_id
        self._register_node(ent_id, data)
        self._write_entity_meta(ent_id, data)
        self._meta_cache[ent_id] = dict(data)
        self._on_meta_read(ent_id)
        self._mark_dirty()
        return ent_id

    def _create_node(self, ref: str, *, meta: Optional[dict] = None, edges: Optional[List[dict]] = None, labels: Optional[List[str]] = None) -> str:
        existing = self._resolve_to_id(ref)
        if existing:
            if labels is None:
                return existing
            existing_labels = set(self._id_index.get(existing, {}).get("labels", []))
            requested_labels = set(labels or [])
            if existing_labels == requested_labels:
                return existing

        vmeta = self.get_virtual_meta(ref)
        if vmeta and labels is None and "labels" in vmeta:
            labels = vmeta["labels"]
        meta = self._sanitize_meta(meta, labels)
        ent_id = _gen_id()
        meta["id"] = ent_id
        meta.setdefault("labels", [])
        self._write_entity_meta(ent_id, meta)
        self._meta_cache[ent_id] = dict(meta)
        self._register_node(ent_id, meta)

        if edges:
            self._add_edges(edges)

        self._mark_dirty()
        return ent_id

    def _delete_node(self, ref: str) -> str:
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return ""
        if not ent_id.startswith("ent_"):
            return ""
        raw = self._read_edges_storage()
        new_raw = [e for e in raw if ent_id not in e.get("nodes", [])]
        self._write_edges_storage(new_raw)
        self._adjacent.pop(ent_id, None)
        for adj_set in self._adjacent.values():
            adj_set.discard(ent_id)
        self._delete_entity_storage(ent_id)
        self._unregister_node(ent_id)
        self._meta_cache.pop(ent_id, None)
        self._remove_cross_edges(ent_id)
        self._mark_dirty()
        return ent_id

    # ==================== traversal helpers ====================

    def cypher(self, query: str, params: dict = None) -> list:
        from storage.cypher import CypherExecutor, parse_cypher
        self._ensure_index()
        return CypherExecutor(self).execute(parse_cypher(query, params=params))

    def _cypher_internal(self, query: str, params: dict = None) -> list:
        from storage.cypher import CypherExecutor, parse_cypher
        self._ensure_index()
        return CypherExecutor(self).execute(parse_cypher(query, params=params), strip_internal=False)

    def _walk_metas(self) -> Iterator[Tuple[str, dict]]:
        self._ensure_index()
        for eid in list(self._id_index.keys()):
            meta = self._get_meta(eid)
            if meta is not None:
                yield (eid, meta)

    def _list_all(self) -> List[Tuple[str, List[str]]]:
        self._ensure_index()
        return [(eid, props.get("labels", [])) for eid, props in self._id_index.items()]

    def _neighbors(self, ref: str) -> List[str]:
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return []
        result = []
        for aid in self._adjacent.get(ent_id, set()):
            props = self._id_index.get(aid)
            if not props:
                continue
            result.append(aid)
        return result

    def _get_edges(self, node_ref: str = None) -> List[dict]:
        self._ensure_index()
        raw_edges = self._read_edges_storage()
        node_id = self._resolve_to_id(node_ref) if node_ref else None
        result = []
        for e in raw_edges:
            nodes = e.get("nodes", [])
            if node_id and node_id not in nodes:
                continue
            result.append({"nodes": list(nodes)})
        return result

    def _add_edges(self, edges: List[dict]):
        self._ensure_index()
        raw = self._read_edges_storage()
        existing_pairs = {frozenset(e.get("nodes", [])) for e in raw}
        for e in edges:
            a_ref = e.get("a", "")
            b_ref = e.get("b", "")
            a_id = self._resolve_to_id(a_ref)
            b_id = self._resolve_to_id(b_ref)
            if not a_id or not b_id or a_id == b_id:
                continue
            pair = frozenset({a_id, b_id})
            if pair in existing_pairs:
                continue
            raw.append({"nodes": [a_id, b_id]})
            existing_pairs.add(pair)
            self._adjacent.setdefault(a_id, set()).add(b_id)
            self._adjacent.setdefault(b_id, set()).add(a_id)
        self._write_edges_storage(raw)
        self._mark_dirty()

    def _clear_edges(self):
        self._write_edges_storage([])
        self._adjacent.clear()
        self._mark_dirty()

    # ==================== cross project ====================

    def _add_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        ref = {"to_project": to_project, "to_entity_id": to_entity_id, "stale": False}
        self._cross_adjacent.setdefault(from_id, []).append(ref)
        self._persist_cross_edge(from_id, to_project, to_entity_id)
        self._mark_dirty()

    def _remove_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        refs = self._cross_adjacent.get(from_id, [])
        self._cross_adjacent[from_id] = [r for r in refs if not (r["to_project"] == to_project and r["to_entity_id"] == to_entity_id)]
        if not self._cross_adjacent[from_id]:
            self._cross_adjacent.pop(from_id, None)
        self._delete_cross_edge(from_id, to_project, to_entity_id)
        self._mark_dirty()

    def _remove_cross_edges(self, from_id: str):
        self._cross_adjacent.pop(from_id, None)
        self._delete_cross_edges_for(from_id)

    def _mark_cross_edge_stale(self, from_id: str, to_project: str, to_entity_id: str):
        refs = self._cross_adjacent.get(from_id, [])
        for r in refs:
            if r["to_project"] == to_project and r["to_entity_id"] == to_entity_id:
                r["stale"] = True
                break
        self._update_cross_edge_stale(from_id, to_project, to_entity_id, stale=True)

    def get_cross_refs(self, ref: str = None) -> List[dict]:
        self._ensure_index()
        results = []
        target_id = self._resolve_to_id(ref) if ref else None
        for from_id, refs in self._cross_adjacent.items():
            if target_id and from_id != target_id:
                continue
            from_name = from_id
            for r in refs:
                results.append({
                    "from": from_name,
                    "to_project": r["to_project"],
                    "to_entity_id": r["to_entity_id"],
                    "stale": r.get("stale", False),
                })
        return results

    def _on_meta_read(self, ent_id: str):
        if ent_id.startswith("ent_") and self._pontis_root:
            meta_path = os.path.join(self._pontis_root, "nodes", ent_id, "_meta.yml")
            try:
                self._read_timestamps[ent_id] = os.path.getmtime(meta_path)
            except OSError:
                pass
