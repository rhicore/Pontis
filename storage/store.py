"""Store — 纯图数据库基类。

节点有属性（dict），边是无向对。只有 labels 是特殊属性（Cypher 语法级匹配）。
project 不存储，由 Cypher 引擎实时附加。

持久化由 GraphBackend 处理，Store 基类直接委托。
子类负责：数据源发现、虚属性计算、名称索引优化等领域逻辑。
"""
import uuid
import fnmatch
import logging
from typing import Dict, Iterator, List, Optional, Tuple

from storage.backends import GraphBackend
from storage.config import SourceConfig

logger = logging.getLogger(__name__)

_BASE_INTERNAL_FIELDS = {"_id"}


def _gen_id() -> str:
    return f"ent_{uuid.uuid4().hex[:8]}"


class Store:
    """纯图数据库基类。

    持久化通过注入的 GraphBackend 实现。
    子类覆写 hook 方法注入领域逻辑（虚属性、名称索引、数据访问等）。
    """

    def __init__(self, source_config: SourceConfig, backend: GraphBackend):
        self._source_config = source_config
        self._backend = backend
        self._backend.connect()

        self._meta_cache: Dict[str, Optional[dict]] = {}

        # 主索引
        self._id_index: Dict[str, dict] = {}  # eid → full props

        # 边索引
        self._adjacent: Dict[str, set] = {}

        # 跨项目边索引
        self._cross_adjacent: Dict[str, List[dict]] = {}

        # 虚实体追踪
        self._virtual_ids: set = set()

        # 并发版本控制
        self._last_version: int = -1

        self._index_built = False

    # ==================== 持久化（委托 backend） ====================

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

    # ==================== 数据访问（可选，子类按需覆写） ====================

    def resolve_data_path(self, rel_path: str) -> str:
        """将相对路径解析为绝对路径（或 URI）。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support resolve_data_path. "
            f"This data source has no file-based path resolution."
        )

    def open_db(self, rel_path: str):
        """上下文管理器：打开数据库连接。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support open_db. "
            f"This data source has no file-based database access."
        )

    def open_file(self, rel_path: str, mode='r', **kwargs):
        """上下文管理器：打开文件。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support open_file. "
            f"This data source has no file-based access."
        )

    def data_exists(self, rel_path: str) -> bool:
        """检查数据文件是否存在。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support data_exists. "
            f"This data source has no file-based existence check."
        )

    # ==================== 版本控制（委托 backend） ====================

    def _read_version(self) -> int:
        return self._backend.read_version()

    def _bump_version(self) -> int:
        return self._backend.bump_version()

    # ==================== 跨项目边（委托 backend） ====================

    def _persist_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        self._backend.add_cross_edge(from_id, to_project, to_entity_id)

    def _delete_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        self._backend.remove_cross_edge(from_id, to_project, to_entity_id)

    def _delete_cross_edges_for(self, from_id: str):
        self._backend.remove_cross_edges_for(from_id)

    def _update_cross_edge_stale(self, from_id: str, to_project: str,
                                 to_entity_id: str, *, stale: bool):
        self._backend.set_cross_edge_stale(from_id, to_project, to_entity_id, stale=stale)

    def _load_cross_edges(self) -> Dict[str, List[dict]]:
        return self._backend.read_cross_edges()

    # ==================== 子类可覆写 ====================

    @property
    def internal_fields(self) -> set:
        """内部字段集合，不暴露给外部。子类可覆写。"""
        return set(_BASE_INTERNAL_FIELDS)

    def discover_virtual(self, pattern: str, label: str = None) -> list:
        """发现虚实体。基类默认返回空。"""
        return []

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        """获取虚实体元数据。基类默认返回 None。"""
        return None

    def get_virtual_neighbors(self, key: str) -> list:
        """获取虚实体邻接。基类默认返回空。"""
        return []

    def _materialize_virtual(self, ref: str, vmeta: dict) -> str:
        """将虚节点实体化为持久化节点。子类覆写。"""
        raise NotImplementedError

    def _enrich_meta(self, ent_id: str, meta: dict,
                     include_props=None) -> dict:
        """补充虚属性。基类直接返回原始 meta。"""
        return meta

    def _on_before_persist(self, meta: dict, ename: str):
        """Hook: 实体写入持久化前。基类为空操作。"""

    def _on_meta_read(self, ent_id: str):
        """Hook: 读取实体元数据后。基类为空操作。"""

    def _mark_dirty(self):
        """写操作后调用：递增版本号 + 更新本地缓存。"""
        new_ver = self._bump_version()
        self._last_version = new_ver

    # ==================== Index ====================

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
            if "_labels" in raw:
                raw["_labels"] = normalize_labels(raw["_labels"])
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
        """注册节点到 _id_index。子类覆写以维护额外索引。"""
        self._id_index[ent_id] = props

    def _unregister_node(self, ent_id: str):
        """从 _id_index 移除节点。子类覆写以清理额外索引。"""
        self._id_index.pop(ent_id, None)

    # ==================== Name/ID Helpers ====================

    def _name_to_id(self, entity_name: str) -> Optional[str]:
        """返回该名称对应的 entity ID。子类必须覆写。"""
        raise NotImplementedError

    def _name_to_ids(self, entity_name: str) -> List[str]:
        """返回该名称对应的所有 entity ID。子类必须覆写。"""
        raise NotImplementedError

    def _resolve_to_id(self, ref: str) -> Optional[str]:
        """解析 ref 为 ent_id。子类必须覆写。"""
        raise NotImplementedError

    def _get_labels_by_id(self, ent_id: str) -> List[str]:
        props = self._id_index.get(ent_id)
        if props:
            return props.get("_labels", [])
        return []

    # ==================== Meta Read ====================

    def _get_meta(self, ref: str, include_props: Optional[List[str]] = None,
                 *, _visiting: Optional[set] = None) -> Optional[dict]:
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
            return {k: v for k, v in cached.items() if k not in self.internal_fields}

        raw = self._read_entity_meta(ent_id)
        if raw is None:
            self._meta_cache[ent_id] = None
            return None
        self._meta_cache[ent_id] = dict(raw)
        return {k: v for k, v in raw.items() if k not in self.internal_fields}

    def _get_meta_internal(self, ref: str, include_props: Optional[List[str]],
                           _visiting: set) -> Optional[dict]:
        """基类实现：解析 ref，加载 meta，过滤内部字段。

        子类（如 FSStore）可覆写以添加 label 分组、虚属性补充等。
        """
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

            result = {k: v for k, v in result.items() if k not in self.internal_fields}
            return result

        return None

    def _materialize(self, ref: str) -> str:
        """将虚节点实体化为持久化节点，返回 ent_id。"""
        ent_id = self._resolve_to_id(ref)
        if ent_id:
            if ent_id in self._virtual_ids:
                return self._auto_materialize(ent_id)
            return ent_id

        vmeta = self.get_virtual_meta(ref)
        if vmeta:
            return self._materialize_virtual(ref, vmeta)

        return self._create_node(ref)

    def _auto_materialize(self, virtual_id: str) -> str:
        """将索引中的虚实体转为持久化实体。返回新的 ent_id。"""
        vmeta = dict(self._meta_cache.get(virtual_id, {}))
        props = self._id_index.get(virtual_id, {})
        labels = vmeta.get("_labels", []) or props.get("_labels", [])

        new_id = _gen_id()
        vmeta["_id"] = new_id
        vmeta.setdefault("_labels", labels)

        self._write_entity_meta(new_id, vmeta)
        self._meta_cache[new_id] = dict(vmeta)
        self._register_node(new_id, vmeta)

        old_adj = self._adjacent.pop(virtual_id, set())
        self._adjacent[new_id] = old_adj
        for adj_set in self._adjacent.values():
            if virtual_id in adj_set:
                adj_set.discard(virtual_id)
                adj_set.add(new_id)

        self._virtual_ids.discard(virtual_id)
        self._meta_cache.pop(virtual_id, None)
        self._unregister_node(virtual_id)
        self._mark_dirty()

        return new_id

    def _read_raw(self, ent_id: str) -> Optional[dict]:
        return self._read_entity_meta(ent_id)

    # ==================== Meta Write ====================

    def _set_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)

        if ent_id and ent_id in self._virtual_ids:
            ent_id = self._auto_materialize(ent_id)

        if ent_id:
            existing = self._read_raw(ent_id) or {}
            existing.update(data)
        else:
            ent_id = self._materialize(ref)
            existing = self._read_raw(ent_id) or {}
            existing.update(data)

        existing.setdefault("_labels", [])

        self._register_node(ent_id, existing)

        self._write_entity_meta(ent_id, existing)
        self._meta_cache[ent_id] = dict(existing)
        self._on_meta_read(ent_id)
        self._mark_dirty()

    def _put_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)

        if ent_id:
            pass
        else:
            ent_id = self._materialize(ref)

        data["_id"] = ent_id
        data.setdefault("_labels", [])

        self._register_node(ent_id, data)

        self._write_entity_meta(ent_id, data)
        self._meta_cache[ent_id] = dict(data)
        self._on_meta_read(ent_id)
        self._mark_dirty()

    def _apply_labels(self, data: dict, labels: Optional[List[str]]) -> dict:
        data["_labels"] = list(labels) if labels is not None else []
        return data

    # ==================== Cypher Entry Point ====================

    def cypher(self, query: str, params: dict = None) -> list:
        """执行 Cypher 查询。所有图操作的统一入口。"""
        from storage.cypher import parse_cypher, CypherExecutor
        self._ensure_index()
        return CypherExecutor(self).execute(parse_cypher(query, params=params))

    # ==================== Iteration ====================

    def _walk_metas(self) -> Iterator[Tuple[str, dict]]:
        self._ensure_index()
        for eid in list(self._id_index.keys()):
            meta = self._get_meta(eid)
            if meta is not None:
                yield (eid, meta)

    # ==================== Node Operations ====================

    def _create_node(self, ref: str, *, meta: Optional[dict] = None,
                    edges: Optional[List[dict]] = None,
                    labels: Optional[List[str]] = None) -> str:
        existing = self._resolve_to_id(ref)
        if existing:
            if existing in self._virtual_ids:
                existing = self._auto_materialize(existing)
            return existing

        meta = meta or {}

        vmeta = self.get_virtual_meta(ref)
        if vmeta:
            merged = dict(vmeta)
            merged.update(meta)
            meta = merged
            if labels is None and "_labels" in vmeta:
                labels = vmeta["_labels"]

        self._apply_labels(meta, labels)

        if "--" in ref:
            parts = ref.split("--")
            ename = parts[-1]
            parent_name = parts[-2]
        else:
            ename = ref
            parent_name = None

        self._on_before_persist(meta, ename)

        ent_id = _gen_id()
        meta["_id"] = ent_id
        meta.setdefault("_labels", [])

        self._write_entity_meta(ent_id, meta)
        self._meta_cache[ent_id] = dict(meta)
        self._register_node(ent_id, meta)

        if parent_name:
            auto_edge = {"a": parent_name, "b": ename}
            all_edges = [auto_edge]
            if edges:
                all_edges.extend(edges)
            if "--" in ref and len(parts) > 2:
                for i in range(len(parts) - 2):
                    all_edges.append({"a": parts[i], "b": parts[i + 1]})
            self._add_edges(all_edges)
        elif edges:
            self._add_edges(edges)

        self._mark_dirty()
        return ent_id

    # ==================== Primitive Queries ====================

    def _list_all(self) -> List[Tuple[str, List[str]]]:
        """返回所有节点的 (ent_id, labels) 列表。子类可覆写返回名字。"""
        self._ensure_index()
        results = []
        for eid, props in self._id_index.items():
            results.append((eid, props.get("_labels", [])))
        return results

    def _neighbors(self, ref: str) -> List[str]:
        """返回邻居的 ent_id 列表。子类可覆写返回名字。"""
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return []
        return [aid for aid in self._adjacent.get(ent_id, set())
                if self._id_index.get(aid)]

    # ==================== Edges ====================

    def _get_edges(self, node_ref: str = None) -> List[dict]:
        self._ensure_index()
        raw_edges = self._read_edges_storage()

        node_id = self._resolve_to_id(node_ref) if node_ref else None

        result = []
        for e in raw_edges:
            nodes = e.get("nodes", [])
            if node_id and node_id not in nodes:
                continue

            edge_dict = {
                "nodes": list(nodes),
            }
            result.append(edge_dict)

        return result

    def _add_edges(self, edges: List[dict]):
        self._ensure_index()
        raw = self._read_edges_storage()
        existing_pairs = {frozenset(e.get("nodes", [])) for e in raw}

        for e in edges:
            a_ref = e.get("a", "")
            b_ref = e.get("b", "")
            a_id = a_ref if a_ref.startswith("ent_") else (self._name_to_id(a_ref) or self._resolve_to_id(a_ref))
            if not a_id:
                a_id = self._materialize(a_ref)
            b_id = b_ref if b_ref.startswith("ent_") else (self._name_to_id(b_ref) or self._resolve_to_id(b_ref))
            if not b_id:
                b_id = self._materialize(b_ref)
            if not a_id or not b_id or a_id == b_id:
                continue

            pair = frozenset({a_id, b_id})
            if pair in existing_pairs:
                continue

            entry = {"nodes": [a_id, b_id]}
            raw.append(entry)
            existing_pairs.add(pair)

            self._adjacent.setdefault(a_id, set()).add(b_id)
            self._adjacent.setdefault(b_id, set()).add(a_id)

        self._write_edges_storage(raw)
        self._mark_dirty()

    def _clear_edges(self):
        self._write_edges_storage([])
        self._adjacent.clear()
        self._mark_dirty()

    # ==================== Delete ====================

    def _delete_node(self, ref: str) -> str:
        self._ensure_index()

        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return ""

        if ent_id in self._virtual_ids:
            self._unregister_node(ent_id)
            self._meta_cache.pop(ent_id, None)
            self._virtual_ids.discard(ent_id)
            self._adjacent.pop(ent_id, None)
            for adj_set in self._adjacent.values():
                adj_set.discard(ent_id)
            return ent_id

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

    # ==================== Cross Edges ====================

    def _add_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        ref = {"to_project": to_project, "to_entity_id": to_entity_id, "stale": False}
        self._cross_adjacent.setdefault(from_id, []).append(ref)
        self._persist_cross_edge(from_id, to_project, to_entity_id)
        self._mark_dirty()

    def _remove_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        refs = self._cross_adjacent.get(from_id, [])
        self._cross_adjacent[from_id] = [
            r for r in refs
            if not (r["to_project"] == to_project and r["to_entity_id"] == to_entity_id)
        ]
        if not self._cross_adjacent[from_id]:
            self._cross_adjacent.pop(from_id, None)
        self._delete_cross_edge(from_id, to_project, to_entity_id)
        self._mark_dirty()

    def _remove_cross_edges(self, from_id: str):
        self._cross_adjacent.pop(from_id, None)
        self._delete_cross_edges_for(from_id)

    def _mark_cross_edge_stale(self, from_id: str, to_project: str,
                                to_entity_id: str):
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
