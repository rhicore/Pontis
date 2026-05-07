"""Store — 图谱存储基类。

带边的 KV 存储 + 内存索引。不解析 URN，不路由，不理解 namespace 语义。
查询和路由由 Finder/Workspace 处理。

子类负责实现持久化、虚实体发现、虚属性计算、文件身份检测。
"""
import uuid
import fnmatch
import logging
from abc import ABC, abstractmethod
from typing import Dict, Iterator, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

_INTERNAL_FIELDS = {"_id", "_inode", "_entity_name"}


def _gen_id() -> str:
    return f"ent_{uuid.uuid4().hex[:8]}"


class Store(ABC):
    """带边的 KV 存储 + 内存索引（基类）。

    子类必须实现所有抽象方法。
    """

    def __init__(self):
        # 缓存
        self._meta_cache: Dict[str, Optional[dict]] = {}
        self._edges_cache: Optional[List[dict]] = None
        self._read_timestamps: Dict[str, float] = {}

        # 索引
        self._id_index: Dict[str, str] = {}
        self._inode_index: Dict[int, str] = {}
        self._name_index: Dict[str, Union[str, List[str]]] = {}

        # 边索引
        self._adjacent: Dict[str, set] = {}

        # 延迟构建索引
        self._index_built = False

    # ==================== 抽象属性 ====================

    @property
    @abstractmethod
    def project_path(self) -> str:
        """项目根路径。"""

    @property
    @abstractmethod
    def pontis_exists(self) -> bool:
        """项目存储是否存在。"""

    @property
    @abstractmethod
    def index_root(self) -> str:
        """索引目录路径。"""

    @property
    def prop_registry(self) -> dict:
        """label → {prop_name: callable} 注册表。子类覆写。"""
        return {}

    @property
    def dir_props(self) -> dict:
        """目录虚属性组。子类覆写。"""
        return {}

    @property
    def common_file_props(self) -> dict:
        """通用文件虚属性组（fallback）。子类覆写。"""
        return {}

    # ==================== 抽象方法：持久化 ====================

    @abstractmethod
    def _scan_entities(self) -> List[Tuple[str, dict]]:
        """扫描存储中所有实体，返回 [(ent_id, raw_meta)]。"""

    @abstractmethod
    def _read_entity_meta(self, ent_id: str) -> Optional[dict]:
        """读取单个实体的元数据。"""

    @abstractmethod
    def _write_entity_meta(self, ent_id: str, data: dict):
        """写入单个实体的元数据。"""

    @abstractmethod
    def _delete_entity_storage(self, ent_id: str):
        """删除实体的存储。"""

    @abstractmethod
    def _read_edges_storage(self) -> List[dict]:
        """读取边数据。"""

    @abstractmethod
    def _write_edges_storage(self, edges: List[dict]):
        """写入边数据。"""

    # ==================== 抽象方法：虚实体/虚属性 ====================

    def discover_virtual(self, pattern: str,
                         label: str = None) -> list:
        """发现虚实体。基类默认返回空。"""
        return []

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        """获取虚实体元数据。基类默认返回 None。"""
        return None

    def get_virtual_neighbors(self, key: str) -> list:
        """获取虚实体邻接。基类默认返回空。"""
        return []

    @abstractmethod
    def _materialize_virtual(self, ref: str, vmeta: dict) -> str:
        """将虚节点实体化为持久化节点，返回 ent_id。

        子类决定哪些虚字段需要持久化（如 FS 的 _inode、S3 的 _etag 等）。
        """

    @abstractmethod
    def _enrich_meta(self, ent_id: str, meta: dict,
                     include_props=None) -> dict:
        """补充虚属性。返回补充后的完整 meta。"""

    # ==================== 抽象方法：文件身份 ====================

    @abstractmethod
    def _detect_file_identity(self, meta: dict, ename: str) -> Optional[int]:
        """检测文件身份（如 inode）。返回身份标识或 None。"""

    # ==================== 抽象方法：时间戳/cache ====================

    @abstractmethod
    def _record_read_timestamp(self, ent_id: str):
        """记录读取时间戳。"""

    @abstractmethod
    def _check_stale(self, ent_id: str) -> Optional[str]:
        """检查实体是否在读取后被修改。"""

    @abstractmethod
    def cache_path(self, *parts: str) -> str:
        """返回缓存文件路径。"""

    @abstractmethod
    def cache_find(self, pattern: str) -> list:
        """查找缓存文件。"""

    # ==================== Index ====================

    def _ensure_index(self):
        if self._index_built:
            return
        self._build_index()

    def _build_index(self):
        from storage.labels import normalize_labels
        self._id_index.clear()
        self._inode_index.clear()
        self._name_index.clear()
        self._adjacent.clear()

        for ent_id, raw in self._scan_entities():
            # 归一化旧格式标签 "col/INT" → ["col", "INT"]
            if "_labels" in raw:
                raw["_labels"] = normalize_labels(raw["_labels"])
                self._meta_cache[ent_id] = raw

            ename = raw.get("_entity_name", "")
            inode = raw.get("_inode")

            self._id_index[ent_id] = ename

            if ename in self._name_index:
                existing = self._name_index[ename]
                if isinstance(existing, list):
                    existing.append(ent_id)
                else:
                    self._name_index[ename] = [existing, ent_id]
            else:
                self._name_index[ename] = ent_id

            if inode is not None:
                self._inode_index[inode] = ent_id

        for e in self._read_edges_storage():
            nodes = e.get("nodes", [])
            for nid in nodes:
                for other in nodes:
                    if other != nid:
                        self._adjacent.setdefault(nid, set()).add(other)

        self._index_built = True

    def _register_node(self, ent_id: str, entity_name: str, inode=None):
        self._id_index[ent_id] = entity_name

        if entity_name in self._name_index:
            existing = self._name_index[entity_name]
            if isinstance(existing, list):
                if ent_id not in existing:
                    existing.append(ent_id)
            else:
                if ent_id != existing:
                    self._name_index[entity_name] = [existing, ent_id]
        else:
            self._name_index[entity_name] = ent_id

        if inode is not None:
            self._inode_index[inode] = ent_id

    def _unregister_node(self, ent_id: str):
        ename = self._id_index.pop(ent_id, None)
        if ename:
            ent_ids = self._name_index.get(ename)
            if isinstance(ent_ids, list):
                ent_ids = [eid for eid in ent_ids if eid != ent_id]
                if len(ent_ids) == 1:
                    self._name_index[ename] = ent_ids[0]
                elif len(ent_ids) == 0:
                    self._name_index.pop(ename, None)
            elif ent_ids == ent_id:
                self._name_index.pop(ename, None)

        to_remove = [inode for inode, eid in self._inode_index.items() if eid == ent_id]
        for inode in to_remove:
            del self._inode_index[inode]

    # ==================== Name/ID Helpers ====================

    def _name_to_id(self, entity_name: str) -> Optional[str]:
        """返回该名称对应的第一个 entity ID（重名时取首个）。"""
        ent_ids = self._name_index.get(entity_name)
        if ent_ids is None:
            return None
        if isinstance(ent_ids, list):
            return ent_ids[0]
        return ent_ids

    def _name_to_ids(self, entity_name: str) -> List[str]:
        """返回该名称对应的所有 entity ID。"""
        ent_ids = self._name_index.get(entity_name)
        if ent_ids is None:
            return []
        if isinstance(ent_ids, list):
            return list(ent_ids)
        return [ent_ids]

    def _resolve_to_id(self, ref: str) -> Optional[str]:
        eid = self._name_to_id(ref)
        if eid:
            return eid
        if ref.startswith("ent_") and ref in self._id_index:
            return ref
        return None

    def _get_labels_by_id(self, ent_id: str) -> List[str]:
        if ent_id in self._meta_cache:
            cached = self._meta_cache[ent_id]
            return cached.get("_labels", []) if cached else []
        raw = self._read_entity_meta(ent_id)
        return raw.get("_labels", []) if raw else []

    # ==================== Meta Read ====================

    def get_meta(self, ref: str, include_props: Optional[List[str]] = None,
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
            return {k: v for k, v in cached.items() if k not in _INTERNAL_FIELDS}

        raw = self._read_entity_meta(ent_id)
        if raw is None:
            self._meta_cache[ent_id] = None
            return None
        self._meta_cache[ent_id] = dict(raw)
        return {k: v for k, v in raw.items() if k not in _INTERNAL_FIELDS}

    def _get_meta_internal(self, ref: str, include_props: Optional[List[str]],
                           _visiting: set) -> Optional[dict]:
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
                self._record_read_timestamp(ent_id)

            result = {k: v for k, v in result.items() if k not in _INTERNAL_FIELDS}

            # 图谱边虚属性：按邻接节点 label 首段自动分组
            ename = self._id_index.get(ent_id, "")
            for adj_id in self._adjacent.get(ent_id, set()):
                adj_name = self._id_index.get(adj_id)
                if adj_name is None:
                    continue
                adj_labels = self._get_labels_by_id(adj_id)
                group_key = None
                for lbl in adj_labels:
                    group_key = lbl
                    break
                if group_key is None:
                    group_key = adj_name.rsplit(".", 1)[-1] if "." in adj_name else adj_name
                if group_key not in result:
                    result[group_key] = [adj_name]
                elif isinstance(result[group_key], list):
                    result[group_key].append(adj_name)

            # 虚属性补充
            if include_props is None or len(include_props) > 0:
                result = self._enrich_meta(ent_id, result, include_props=include_props)

            return result

        # 无持久化节点 → 子类可覆写 fallback
        return self._get_meta_fallback(ref, include_props, _visiting)

    def _get_meta_fallback(self, ref: str, include_props=None,
                           _visiting=None) -> Optional[dict]:
        """未找到持久化节点时的 fallback。基类返回 None，子类可覆写。"""
        return None

    def _materialize(self, ref: str) -> str:
        """将虚节点实体化为持久化节点，返回 ent_id。

        若已持久化则直接返回；若为虚节点则用虚信息创建；
        否则走 create_node 创建全新空节点。
        """
        ent_id = self._resolve_to_id(ref)
        if ent_id:
            return ent_id

        vmeta = self.get_virtual_meta(ref)
        if vmeta:
            return self._materialize_virtual(ref, vmeta)

        return self.create_node(ref)

    def _read_raw(self, ent_id: str) -> Optional[dict]:
        return self._read_entity_meta(ent_id)

    # ==================== Meta Write ====================

    def set_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)

        if ent_id:
            stale_err = self._check_stale(ent_id)
            if stale_err:
                raise RuntimeError(stale_err)
            existing = self._read_raw(ent_id) or {}
            existing.update(data)
        else:
            ent_id = self._materialize(ref)
            existing = self._read_raw(ent_id) or {}
            existing.update(data)

        existing.setdefault("_entity_name", ref)
        existing.setdefault("_labels", [])

        inode = existing.get("_inode")
        self._register_node(ent_id, ref, inode=inode)

        self._write_entity_meta(ent_id, existing)
        self._meta_cache[ent_id] = dict(existing)
        self._record_read_timestamp(ent_id)

    def put_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)

        if ent_id:
            stale_err = self._check_stale(ent_id)
            if stale_err:
                raise RuntimeError(stale_err)
        else:
            ent_id = self._materialize(ref)

        data["_id"] = ent_id
        data.setdefault("_entity_name", ref)
        data.setdefault("_labels", [])

        inode = data.get("_inode")
        self._register_node(ent_id, ref, inode=inode)

        self._write_entity_meta(ent_id, data)
        self._meta_cache[ent_id] = dict(data)
        self._record_read_timestamp(ent_id)

    def _apply_labels(self, data: dict, labels: Optional[List[str]]) -> dict:
        data["_labels"] = list(labels) if labels is not None else []
        return data

    # ==================== Legacy Compatibility ====================

    def find_nodes(self, pattern: str) -> List[str]:
        """兼容旧 extractor 的 find_nodes。"""
        self._ensure_index()

        segments = []
        current_buf = []
        i = 0
        while i < len(pattern):
            if pattern[i] == ':' and i + 1 < len(pattern) and pattern[i + 1] == ':':
                seg_text = "".join(current_buf)
                if seg_text:
                    segments.append(seg_text)
                current_buf = []
                i += 2
                continue
            current_buf.append(pattern[i])
            i += 1
        seg_text = "".join(current_buf)
        if seg_text:
            segments.append(seg_text)

        if not segments:
            return []

        if len(segments) == 1:
            return self._legacy_single_match(segments[0])

        return self._legacy_traverse_match(segments)

    def _legacy_single_match(self, pattern: str) -> List[str]:
        name_pattern, label_filter = self._split_pattern_label(pattern)

        results = []
        seen = set()
        for eid, ename in self._id_index.items():
            if fnmatch.fnmatch(ename, name_pattern):
                if label_filter:
                    labels = self._get_labels_by_id(eid)
                    if not self._label_matches_legacy(labels, label_filter):
                        continue
                if ename not in seen:
                    seen.add(ename)
                    results.append(ename)
        return results

    def _legacy_traverse_match(self, segments: List[str]) -> List[str]:
        current_ids = set()

        first = segments[0]
        first_pat, first_lbl = self._split_pattern_label(first)
        for eid, ename in self._id_index.items():
            if fnmatch.fnmatch(ename, first_pat):
                if first_lbl:
                    labels = self._get_labels_by_id(eid)
                    if not self._label_matches_legacy(labels, first_lbl):
                        continue
                current_ids.add(eid)

        for seg in segments[1:]:
            seg_pat, seg_lbl = self._split_pattern_label(seg)
            next_ids = set()
            for eid in current_ids:
                for adj_id in self._adjacent.get(eid, set()):
                    adj_name = self._id_index.get(adj_id, "")
                    if fnmatch.fnmatch(adj_name, seg_pat):
                        if seg_lbl:
                            labels = self._get_labels_by_id(adj_id)
                            if not self._label_matches_legacy(labels, seg_lbl):
                                continue
                        next_ids.add(adj_id)
            current_ids = next_ids

        return [self._id_index[eid] for eid in current_ids
                if self._id_index.get(eid)]

    @staticmethod
    def _split_pattern_label(pattern: str):
        for sep_idx in range(len(pattern) - 1, 0, -1):
            if pattern[sep_idx] == ':':
                candidate = pattern[sep_idx + 1:]
                if candidate and not any(c in candidate for c in '*?[]:.'):
                    return pattern[:sep_idx], candidate
        return pattern, None

    @staticmethod
    def _label_matches_legacy(entity_labels: List[str], query: str) -> bool:
        from storage.labels import label_matches
        return label_matches(entity_labels, query)

    def find_connected(self, ref: str, pattern: str = "*") -> List[str]:
        return self.neighbors(ref) if pattern == "*" else [
            n for n in self.neighbors(ref)
            if fnmatch.fnmatch(n, pattern)
        ]

    def walk_metas(self) -> Iterator[Tuple[str, dict]]:
        self._ensure_index()
        for ename in self._name_index:
            meta = self.get_meta(ename)
            if meta is not None:
                yield (ename, meta)

    def resolve_ref(self, ref: str) -> str:
        if ref.startswith("ent_"):
            ename = self._id_index.get(ref)
            if ename is None:
                raise KeyError(f"Unknown node ID: {ref}")
            return ename

        if "::" in ref:
            parts = ref.split("::")
            current_ids = set()
            for eid, ename in self._id_index.items():
                if ename == parts[0]:
                    current_ids.add(eid)
            for seg in parts[1:]:
                next_ids = set()
                for eid in current_ids:
                    for adj_id in self._adjacent.get(eid, set()):
                        adj_name = self._id_index.get(adj_id, "")
                        if fnmatch.fnmatch(adj_name, seg):
                            next_ids.add(adj_id)
                current_ids = next_ids
            if len(current_ids) == 1:
                return self._id_index[next(iter(current_ids))]
            if len(current_ids) == 0:
                raise KeyError(f"No match: {ref}")
            raise KeyError(f"Ambiguous: {ref}")

        return ref

    # ==================== Node Operations ====================

    def create_node(self, ref: str, *, meta: Optional[dict] = None,
                    edges: Optional[List[dict]] = None,
                    labels: Optional[List[str]] = None) -> str:
        # 幂等：已持久化则直接返回
        existing = self._resolve_to_id(ref)
        if existing:
            return existing

        meta = meta or {}

        # 虚节点 → 合并用户的 labels/meta 后实体化
        vmeta = self.get_virtual_meta(ref)
        if vmeta:
            merged = dict(vmeta)
            merged.update(meta)
            if "_inode" in vmeta and "_inode" not in meta:
                merged["_inode"] = vmeta["_inode"]
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

        inode = self._detect_file_identity(meta, ename)
        if inode is not None:
            meta["_inode"] = inode

        ent_id = _gen_id()
        meta["_id"] = ent_id
        meta["_entity_name"] = ename
        meta.setdefault("_labels", [])

        inode = meta.get("_inode")
        self._register_node(ent_id, ename, inode=inode)
        self._write_entity_meta(ent_id, meta)
        self._meta_cache[ent_id] = dict(meta)

        if parent_name:
            auto_edge = {"a": parent_name, "b": ename}
            all_edges = [auto_edge]
            if edges:
                all_edges.extend(edges)
            if "--" in ref and len(parts) > 2:
                for i in range(len(parts) - 2):
                    all_edges.append({"a": parts[i], "b": parts[i + 1]})
            self.add_edges(all_edges)
        elif edges:
            self.add_edges(edges)

        return ent_id

    def node_exists(self, ref: str) -> bool:
        return self._resolve_to_id(ref) is not None

    # ==================== Primitive Queries (for Finder) ====================

    def list_all(self) -> List[Tuple[str, List[str]]]:
        self._ensure_index()
        results = []
        for eid, ename in self._id_index.items():
            labels = self._get_labels_by_id(eid)
            results.append((ename, labels))
        return results

    def neighbors(self, ref: str) -> List[str]:
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return []
        return [self._id_index.get(aid) for aid in self._adjacent.get(ent_id, set())
                if self._id_index.get(aid)]

    # ==================== Edges ====================

    def get_edges(self, node_ref: str = None) -> List[dict]:
        self._ensure_index()
        raw_edges = self._read_edges_storage()

        node_id = self._resolve_to_id(node_ref) if node_ref else None

        result = []
        for e in raw_edges:
            nodes = e.get("nodes", [])
            if node_id and node_id not in nodes:
                continue

            edge_dict = {
                "nodes": [self._id_index.get(nid, nid) for nid in nodes],
            }
            result.append(edge_dict)

        return result

    def add_edges(self, edges: List[dict]):
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

    def clear_edges(self):
        self._write_edges_storage([])
        self._adjacent.clear()

    # ==================== Delete ====================

    def delete_node(self, ref: str) -> str:
        self._ensure_index()

        ent_id = self._resolve_to_id(ref)
        if not ent_id or not ent_id.startswith("ent_"):
            return ""

        ename = self._id_index.get(ent_id, "")

        raw = self._read_edges_storage()
        new_raw = [e for e in raw if ent_id not in e.get("nodes", [])]
        self._write_edges_storage(new_raw)

        self._adjacent.pop(ent_id, None)
        for adj_set in self._adjacent.values():
            adj_set.discard(ent_id)

        self._delete_entity_storage(ent_id)
        self._unregister_node(ent_id)
        self._meta_cache.pop(ent_id, None)

        return ename
