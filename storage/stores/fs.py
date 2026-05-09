"""FSStore — 文件系统 Store 实现（POSIX / Windows）。

文件系统数据源发现、目录虚节点、虚属性计算。
持久化由注入的 GraphBackend 处理。
"""
import os
import fnmatch
import sqlite3
import logging
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from storage.store import Store

logger = logging.getLogger(__name__)


class FSStore(Store):
    """文件系统 Store（POSIX / Windows）。

    负责文件系统数据源发现、虚实体管理、名称索引优化。
    持久化由注入的 GraphBackend 处理。
    """

    def __init__(self, source_config, backend):
        super().__init__(source_config, backend)
        self._project_path = os.path.abspath(source_config.path)
        self._project_name = os.path.basename(self._project_path)
        self._pontis_root = os.path.join(self._project_path, ".pontis")
        self._nodes_root = os.path.join(self._pontis_root, "nodes")  # legacy, read-only
        self._backend_db_path = getattr(backend, '_db_path', None)
        self._dirs_cache: Dict[str, str] = {}  # full_rel_path → bare_name
        self._name_index: Dict[str, object] = {}  # ename → ent_id | [ent_id, ...]
        self._inode_index: Dict[int, str] = {}
        self._read_timestamps: Dict[str, float] = {}

    def _is_backend_file(self, fp: str) -> bool:
        """检查文件是否是图数据库后端文件（含 WAL/SHM 附属文件）。"""
        if not self._backend_db_path:
            return False
        absp = os.path.abspath(fp)
        db_base = os.path.abspath(self._backend_db_path)
        return absp == db_base or absp.startswith(db_base + '-')

    # ==================== Properties ====================

    # ==================== Legacy Compatibility ====================

    # ==================== Properties ====================

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def pontis_exists(self) -> bool:
        return os.path.exists(self._pontis_root)

    @property
    def index_root(self) -> str:
        return os.path.join(self._pontis_root, "_index")

    @property
    def prop_registry(self) -> dict:
        from storage.stores.modules.fs import PROP_REGISTRY
        return PROP_REGISTRY

    @property
    def dir_props(self) -> dict:
        from storage.stores.modules.fs import DIR_PROPS
        return DIR_PROPS

    @property
    def common_file_props(self) -> dict:
        from storage.stores.modules.fs import COMMON_FILE_PROPS
        return COMMON_FILE_PROPS

    # ==================== Cache ====================

    def cache_path(self, *parts: str) -> str:
        cache_dir = os.path.join(self._pontis_root, "cache")
        path = os.path.join(cache_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def cache_find(self, pattern: str) -> list:
        cache_dir = os.path.join(self._pontis_root, "cache")
        if not os.path.isdir(cache_dir):
            return []
        results = []
        for root, _, files in os.walk(cache_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, cache_dir)
                if fnmatch.fnmatch(rel, pattern):
                    results.append(full)
        return results

    # ==================== 基类钩子覆写 ====================

    @property
    def internal_fields(self) -> set:
        return {"_id", "_inode"}

    def _register_node(self, ent_id: str, props: dict):
        super()._register_node(ent_id, props)
        ename = props.get("name", "")
        if ename:
            if ename in self._name_index:
                existing = self._name_index[ename]
                if isinstance(existing, list):
                    if ent_id not in existing:
                        existing.append(ent_id)
                else:
                    if ent_id != existing:
                        self._name_index[ename] = [existing, ent_id]
            else:
                self._name_index[ename] = ent_id
        inode = props.get("_inode")
        if inode is not None:
            self._inode_index[inode] = ent_id

    def _unregister_node(self, ent_id: str):
        props = self._id_index.get(ent_id, {})
        ename = props.get("name", "")
        if ename:
            ent_ids = self._name_index.get(ename)
            if isinstance(ent_ids, list):
                ent_ids = [eid for eid in ent_ids if eid != ent_id]
                if len(ent_ids) == 1:
                    self._name_index[ename] = ent_ids[0]
                elif len(ent_ids) == 0:
                    self._name_index.pop(ename, None)
                else:
                    self._name_index[ename] = ent_ids
            elif ent_ids == ent_id:
                self._name_index.pop(ename, None)
        super()._unregister_node(ent_id)
        to_remove = [inode for inode, eid in self._inode_index.items() if eid == ent_id]
        for inode in to_remove:
            del self._inode_index[inode]

    def _on_before_persist(self, meta: dict, ename: str):
        meta.setdefault("name", ename)
        physical = os.path.join(self._project_path, ename)
        try:
            if os.path.exists(physical) and os.path.isfile(physical):
                meta["_inode"] = os.stat(physical).st_ino
        except OSError:
            pass

    def _on_meta_read(self, ent_id: str):
        if ent_id.startswith("ent_"):
            meta_path = os.path.join(self._nodes_root, ent_id, "_meta.yml")
            try:
                self._read_timestamps[ent_id] = os.path.getmtime(meta_path)
            except OSError:
                pass

    # ==================== 名称解析（O(1) 覆写） ====================

    def _name_to_id(self, entity_name: str) -> Optional[str]:
        """O(1) 名称查找。重名优先级：有标签且有邻居 > 有标签 > 无标签。"""
        if not self._index_built:
            self._ensure_index()
        ent_ids = self._name_index.get(entity_name)
        if ent_ids is None:
            return None
        if isinstance(ent_ids, list):
            best_labeled = None
            for eid in ent_ids:
                labels = self._get_labels_by_id(eid)
                if labels:
                    if self._adjacent.get(eid):
                        return eid
                    if best_labeled is None:
                        best_labeled = eid
            return best_labeled or ent_ids[0]
        return ent_ids

    def _name_to_ids(self, entity_name: str) -> List[str]:
        if not self._index_built:
            self._ensure_index()
        ent_ids = self._name_index.get(entity_name)
        if ent_ids is None:
            return []
        if isinstance(ent_ids, list):
            return list(ent_ids)
        return [ent_ids]

    def _resolve_to_id(self, ref: str) -> Optional[str]:
        if not self._index_built:
            self._ensure_index()
        eid = self._name_to_id(ref)
        if eid:
            return eid
        if ref in self._id_index:
            return ref
        if "--" in ref:
            parts = ref.split("--")
            current_ids = self._name_to_ids(parts[0])
            matched_prefix = bool(current_ids)
            for seg in parts[1:]:
                if not current_ids:
                    break
                next_ids = []
                for current_id in current_ids:
                    for adj_id in self._adjacent.get(current_id, set()):
                        adj_props = self._id_index.get(adj_id, {})
                        if adj_props.get("name") == seg:
                            next_ids.append(adj_id)
                current_ids = next_ids
                if current_ids:
                    matched_prefix = True
            if len(current_ids) == 1:
                return current_ids[0]
            if len(current_ids) > 1:
                best_labeled = None
                for candidate in current_ids:
                    labels = self._get_labels_by_id(candidate)
                    if labels:
                        if self._adjacent.get(candidate):
                            return candidate
                        if best_labeled is None:
                            best_labeled = candidate
                if best_labeled:
                    return best_labeled
                return current_ids[0]
            if matched_prefix:
                return None
            bare = parts[-1]
            eid = self._name_to_id(bare)
            if eid:
                return eid
        return None

    def resolve_ref(self, ref: str) -> str:
        if ref.startswith("ent_"):
            props = self._id_index.get(ref)
            if props is None:
                raise KeyError(f"Unknown node ID: {ref}")
            return props.get("name", "")

        if "::" in ref:
            import fnmatch as _fnmatch
            parts = ref.split("::")
            current_ids = set()
            for eid, props in self._id_index.items():
                if props.get("name", "") == parts[0]:
                    current_ids.add(eid)
            for seg in parts[1:]:
                next_ids = set()
                for eid in current_ids:
                    for adj_id in self._adjacent.get(eid, set()):
                        adj_props = self._id_index.get(adj_id)
                        if adj_props and _fnmatch.fnmatch(adj_props.get("name", ""), seg):
                            next_ids.add(adj_id)
                current_ids = next_ids
            if len(current_ids) == 1:
                return self._id_index[next(iter(current_ids))].get("name", "")
            if len(current_ids) == 0:
                raise KeyError(f"No match: {ref}")
            raise KeyError(f"Ambiguous: {ref}")

        return ref

    def _list_all(self):
        self._ensure_index()
        results = []
        for eid, props in self._id_index.items():
            results.append((props.get("name", eid), props.get("_labels", [])))
        return results

    def _neighbors(self, ref: str):
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return []
        return [self._id_index.get(aid, {}).get("name", aid)
                for aid in self._adjacent.get(ent_id, set())
                if self._id_index.get(aid)]

    def _get_edges(self, node_ref=None):
        self._ensure_index()
        raw_edges = self._backend.read_edges()
        node_id = self._resolve_to_id(node_ref) if node_ref else None
        result = []
        for e in raw_edges:
            nodes = e.get("nodes", [])
            if node_id and node_id not in nodes:
                continue
            edge_dict = {
                "nodes": [self._id_index.get(nid, {}).get("name", nid) for nid in nodes],
            }
            result.append(edge_dict)
        return result

    def _walk_metas(self):
        self._ensure_index()
        seen = set()
        for eid, props in self._id_index.items():
            name = props.get("name", "")
            if name and name not in seen:
                seen.add(name)
                meta = self._get_meta(name)
                if meta is not None:
                    yield (name, meta)

    def _auto_materialize(self, virtual_id: str) -> str:
        vmeta = dict(self._meta_cache.get(virtual_id, {}))
        props = self._id_index.get(virtual_id, {})
        name = props.get("name", "")
        labels = vmeta.get("_labels", []) or props.get("_labels", [])

        from storage.store import _gen_id
        new_id = _gen_id()
        vmeta["_id"] = new_id
        vmeta["name"] = name
        vmeta.setdefault("_labels", labels)

        self._backend.write_node(new_id, vmeta)
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

    def _set_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)

        if ent_id and ent_id in self._virtual_ids:
            ent_id = self._auto_materialize(ent_id)

        if ent_id:
            existing = self._backend.read_node(ent_id) or {}
            existing.update(data)
        else:
            ent_id = self._materialize(ref)
            existing = self._backend.read_node(ent_id) or {}
            existing.update(data)

        existing.setdefault("name", ref)
        existing.setdefault("_labels", [])

        self._register_node(ent_id, existing)

        self._backend.write_node(ent_id, existing)
        self._meta_cache[ent_id] = dict(existing)
        self._on_meta_read(ent_id)
        self._mark_dirty()

    def _put_meta(self, ref: str, data: dict):
        ent_id = self._resolve_to_id(ref)

        if not ent_id:
            ent_id = self._materialize(ref)

        data["_id"] = ent_id
        data.setdefault("name", ref)
        data.setdefault("_labels", [])

        self._register_node(ent_id, data)

        self._backend.write_node(ent_id, data)
        self._meta_cache[ent_id] = dict(data)
        self._on_meta_read(ent_id)
        self._mark_dirty()

    def _delete_node(self, ref: str) -> str:
        self._ensure_index()

        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return ""

        ename = self._id_index.get(ent_id, {}).get("name", "")

        if ent_id in self._virtual_ids:
            self._unregister_node(ent_id)
            self._meta_cache.pop(ent_id, None)
            self._virtual_ids.discard(ent_id)
            self._adjacent.pop(ent_id, None)
            for adj_set in self._adjacent.values():
                adj_set.discard(ent_id)
            return ename

        if not ent_id.startswith("ent_"):
            return ""

        raw = self._backend.read_edges()
        new_raw = [e for e in raw if ent_id not in e.get("nodes", [])]
        self._backend.write_edges(new_raw)

        self._adjacent.pop(ent_id, None)
        for adj_set in self._adjacent.values():
            adj_set.discard(ent_id)

        self._backend.delete_node(ent_id)
        self._backend.remove_edges_for(ent_id)
        self._unregister_node(ent_id)
        self._meta_cache.pop(ent_id, None)
        self._remove_cross_edges(ent_id)
        self._mark_dirty()

        return ename

    def get_cross_refs(self, ref=None):
        self._ensure_index()
        results = []
        target_id = self._resolve_to_id(ref) if ref else None
        for from_id, refs in self._cross_adjacent.items():
            if target_id and from_id != target_id:
                continue
            from_name = self._id_index.get(from_id, {}).get("name", "")
            for r in refs:
                results.append({
                    "from": from_name,
                    "to_project": r["to_project"],
                    "to_entity_id": r["to_entity_id"],
                    "stale": r.get("stale", False),
                })
        return results

    # ==================== Meta（覆写基类以添加 enrichment） ====================

    def _get_meta_internal(self, ref: str, include_props,
                           _visiting: set) -> Optional[dict]:
        """FSStore 扩展：基类加载 meta 后，添加 label 分组 + 虚属性 + fallback。"""
        result = super()._get_meta_internal(ref, include_props, _visiting)

        if result is not None:
            ent_id = self._resolve_to_id(ref)
            if ent_id:
                # label 分组：邻接节点按首 label 自动分组
                for adj_id in self._adjacent.get(ent_id, set()):
                    adj_props = self._id_index.get(adj_id)
                    if adj_props is None:
                        continue
                    adj_name = adj_props.get("name", "")
                    adj_labels = adj_props.get("_labels", [])
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

        # 文件系统 fallback
        return self._get_meta_fallback(ref, include_props, _visiting)

    # ==================== 数据访问 ====================

    def resolve_data_path(self, rel_path: str) -> str:
        return os.path.join(self._project_path, rel_path)

    @contextmanager
    def open_db(self, rel_path: str):
        conn = sqlite3.connect(self.resolve_data_path(rel_path))
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def open_file(self, rel_path: str, mode='r', **kwargs):
        with open(self.resolve_data_path(rel_path), mode, **kwargs) as f:
            yield f

    def data_exists(self, rel_path: str) -> bool:
        return os.path.exists(self.resolve_data_path(rel_path))

    # ==================== Persistence（覆写基类以添加虚实体支持） ====================

    def _read_entity_meta(self, ent_id: str) -> Optional[dict]:
        if ent_id in self._virtual_ids:
            cached = self._meta_cache.get(ent_id)
            return dict(cached) if cached else None
        return self._backend.read_node(ent_id)

    # ==================== 统一索引（含虚实体） ====================

    def _build_index(self):
        super()._build_index()
        self._add_virtual_to_index()

    def _add_virtual_to_index(self):
        """扫描文件系统，将未持久化的文件和目录加入索引。"""
        import uuid as _uuid
        self._scan_dirs()

        # 收集已持久化实体的 path，用于去重
        persisted_paths = set()
        for eid, raw in self._meta_cache.items():
            if raw and "path" in raw:
                persisted_paths.add(raw["path"])

        # 添加根目录 "." (如果不存在)
        if "." not in self._name_index:
            vid = f"_v_{_uuid.uuid4().hex[:8]}"
            vmeta = {"_labels": ["dir"], "name": "."}
            self._meta_cache[vid] = vmeta
            self._register_node(vid, vmeta)
            self._virtual_ids.add(vid)

        # 添加目录虚实体
        dir_name_map: Dict[str, str] = {}  # vid → rel_path
        for rel_path, bare_name in self._dirs_cache.items():
            if bare_name in self._name_index:
                # 检查已有实体是否就是这个目录
                ent_ids = self._name_to_ids(bare_name)
                if ent_ids:
                    found = False
                    for eid in ent_ids:
                        cached = self._meta_cache.get(eid, {})
                        if cached.get("path", bare_name) == rel_path:
                            found = True
                            break
                    if found:
                        continue
            vid = f"_v_{_uuid.uuid4().hex[:8]}"
            vmeta = {"_labels": ["dir"], "name": bare_name, "path": rel_path}
            self._meta_cache[vid] = vmeta
            self._register_node(vid, vmeta)
            self._virtual_ids.add(vid)
            dir_name_map[vid] = rel_path

        # 添加文件虚实体（os.walk 全项目）
        file_name_map: Dict[str, str] = {}  # vid → rel_path
        for root, dirs, files in os.walk(self._project_path):
            dirs[:] = [d for d in dirs if d != '.pontis']
            for f in files:
                fp = os.path.join(root, f)
                # 忽略图数据库自身，避免嵌套解析
                if self._is_backend_file(fp):
                    continue
                rel = os.path.relpath(fp, self._project_path)
                # 去重：已在持久化中
                if rel in persisted_paths:
                    continue
                # 按名称去重（已有同名实体且 path 匹配）
                bare = os.path.basename(rel)
                ent_ids = self._name_to_ids(bare)
                if ent_ids:
                    found = False
                    for eid in ent_ids:
                        cached = self._meta_cache.get(eid, {})
                        if cached.get("path", bare) == rel:
                            found = True
                            break
                    if found:
                        continue

                # 推断标签
                ext = os.path.splitext(f)[1].lower()
                label_map = {
                    ".db": ["file", "db"], ".sqlite": ["file", "db"],
                    ".sqlite3": ["file", "db"], ".duckdb": ["file", "db"],
                    ".csv": ["file", "csv"], ".tsv": ["file", "csv"],
                    ".json": ["file", "json"], ".jsonl": ["file", "json"],
                    ".yaml": ["file", "yaml"], ".yml": ["file", "yaml"],
                    ".xml": ["file", "xml"], ".toml": ["file", "toml"],
                    ".hcl": ["file", "hcl"],
                }
                labels = label_map.get(ext, ["file"])

                try:
                    inode = os.stat(fp).st_ino
                except OSError:
                    inode = None

                vid = f"_v_{_uuid.uuid4().hex[:8]}"
                meta = {"_labels": labels, "name": bare, "path": rel}
                if inode is not None:
                    meta["_inode"] = inode

                self._meta_cache[vid] = meta
                self._register_node(vid, meta)
                self._virtual_ids.add(vid)
                file_name_map[vid] = rel

        # 构建虚目录邻接
        all_name_map: Dict[str, str] = {}
        all_name_map.update(dir_name_map)
        all_name_map.update(file_name_map)

        # 根目录的子项
        root_entries = set()
        for entry in os.listdir(self._project_path):
            if entry.startswith('.'):
                continue
            root_entries.add(entry)

        root_vid = self._name_to_id(".")
        if root_vid:
            for vid, rel_path in all_name_map.items():
                parent = os.path.dirname(rel_path)
                if parent == ".":
                    self._adjacent.setdefault(root_vid, set()).add(vid)
                    self._adjacent.setdefault(vid, set()).add(root_vid)

        # 子目录的子项
        for dir_vid, dir_rel in dir_name_map.items():
            for vid, rel_path in all_name_map.items():
                if vid == dir_vid:
                    continue
                parent = os.path.dirname(rel_path)
                if parent == dir_rel:
                    self._adjacent.setdefault(dir_vid, set()).add(vid)
                    self._adjacent.setdefault(vid, set()).add(dir_vid)

    def _ensure_storage(self):
        os.makedirs(self._pontis_root, exist_ok=True)

    # ==================== Virtual Entity Discovery ====================

    def discover_virtual(self, pattern: str,
                         label: str = None) -> List[Tuple[str, str, List[str], str]]:
        """发现虚实体。

        Returns:
            [(internal_key, display_name, labels, entity_type), ...]
            entity_type: "dir" | "file"
        """
        results = []
        if label is None or label == "dir":
            results.extend(self._discover_dirs(pattern))
        if label is None or label != "dir":
            results.extend(self._discover_files(pattern))
        return results

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        """获取虚实体 meta（含虚属性）。key 可以是目录相对路径或文件相对路径。"""
        full = os.path.join(self._project_path, key)
        if os.path.isdir(full):
            meta = self._dir_meta(key)
        elif os.path.isfile(full):
            meta = self._file_meta(key)
        else:
            meta = None

        if meta is None:
            return None

        # 补充虚属性
        from storage.enricher import enrich_meta
        entity_path = ""
        file_rel_path = key
        if any(l == "dir" for l in meta.get("_labels", [])):
            file_rel_path = key
        return enrich_meta(meta, self._project_path, file_rel_path, entity_path,
                           store=self)

    def get_virtual_neighbors(self, key: str) -> List[Tuple[str, str, list]]:
        """获取虚实体（目录）的邻接子项。"""
        return self._dir_adjacent(key)

    def _materialize_virtual(self, ref: str, vmeta: dict) -> str:
        """FS 虚节点实体化：保留 _inode 和 _labels，其他虚属性不持久化。"""
        meta = {}
        if "_inode" in vmeta:
            meta["_inode"] = vmeta["_inode"]
        labels = vmeta.get("_labels", [])
        return self._create_node(ref, meta=meta, labels=labels)

    # ==================== Virtual Property Enrichment ====================

    def _enrich_meta(self, ent_id: str, meta: dict,
                     include_props=None) -> dict:
        """为实体补充虚属性。"""
        from storage.enricher import enrich_meta

        # 推断 file_rel_path 和 entity_path
        entity_labels = meta.get("_labels", [])
        ename = self._id_index.get(ent_id, {}).get("name", "")

        # 从邻接文件实体获取 file_rel_path
        file_rel_path = ""
        self._ensure_index()
        for adj_id in self._adjacent.get(ent_id, set()):
            adj_props = self._id_index.get(adj_id, {})
            adj_labels = adj_props.get("_labels", [])
            if "file" in adj_labels:
                file_rel_path = adj_props.get("path",
                    self._meta_cache.get(adj_id, {}).get("path", adj_props.get("name", "")))
                break
        if not file_rel_path:
            file_rel_path = meta.get("path", "")
        entity_path = "" if "file" in entity_labels else ename

        return enrich_meta(meta, self._project_path, file_rel_path, entity_path,
                           include_props=include_props, store=self)

    def _get_meta_fallback(self, ref: str, include_props=None,
                           _visiting=None) -> Optional[dict]:
        """未在索引中找到 ref 时的文件系统 fallback。"""
        physical = os.path.join(self._project_path, ref)
        if os.path.exists(physical):
            from storage.enricher import enrich_meta
            if include_props is None or len(include_props) > 0:
                result = enrich_meta({}, self._project_path, ref, "",
                                     include_props=include_props,
                                     store=self, _visiting=_visiting)
                return result if result else None
        return None

    # ==================== 目录发现 ====================

    def _scan_dirs(self):
        """扫描项目目录树，缓存所有目录。"""
        if self._dirs_cache:
            return
        for root, dirs, _ in os.walk(self._project_path):
            dirs[:] = [d for d in dirs if d != '.pontis']
            for d in dirs:
                full = os.path.join(root, d)
                rel = os.path.relpath(full, self._project_path)
                bare = os.path.basename(rel)
                self._dirs_cache[rel] = bare

    def _discover_dirs(self, pattern: str) -> List[Tuple[str, str, List[str], str]]:
        """发现目录虚节点。返回 [(internal_key, bare_name, ["dir"], "dir")]。"""
        self._scan_dirs()
        results = []
        for rel_path, bare_name in self._dirs_cache.items():
            if fnmatch.fnmatch(bare_name, pattern):
                results.append((rel_path, bare_name, ["dir"], "dir"))
        if fnmatch.fnmatch(".", pattern):
            results.append((".", ".", ["dir"], "dir"))
        return results

    def _discover_files(self, pattern: str) -> List[Tuple[str, str, List[str], str]]:
        """发现未索引的物理文件。返回 [(rel_path, bare_name, [], "file")]。"""
        results = []
        project_path = self._project_path

        def _is_backend(fp):
            return self._is_backend_file(fp)

        if '**' in pattern:
            base = os.path.join(project_path, pattern.split('**')[0] or '')
            if not os.path.isdir(base):
                base = project_path
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != '.pontis']
                for f in files:
                    fp = os.path.join(root, f)
                    if _is_backend(fp):
                        continue
                    rel = os.path.relpath(fp, project_path)
                    if fnmatch.fnmatch(rel, pattern.replace('\\', '/')):
                        results.append(rel)
        else:
            full = os.path.join(project_path, pattern)
            dir_part = os.path.dirname(full)
            pat_part = os.path.basename(full)
            if os.path.isdir(dir_part):
                for f in os.listdir(dir_part):
                    if fnmatch.fnmatch(f, pat_part):
                        fp = os.path.join(dir_part, f)
                        if os.path.isfile(fp) and not _is_backend(fp):
                            rel = os.path.relpath(fp, project_path)
                            if '.pontis' not in rel.split(os.sep):
                                results.append(rel)

        # 过滤掉已在 Store 中注册的实体（按 path 去重）
        self._ensure_index()
        deduped = []
        for rel in results:
            # 先查名称索引（bare name）
            bare = os.path.basename(rel)
            ent_ids = self._name_to_ids(bare)
            if ent_ids:
                # 检查是否有实体的 path 和这个 rel 匹配
                found = False
                for eid in ent_ids:
                    cached = self._meta_cache.get(eid, {})
                    if cached.get("path", bare) == rel:
                        found = True
                        break
                    # 缓存没有则读文件
                    raw = self._read_entity_meta(eid)
                    if raw and raw.get("path", bare) == rel:
                        found = True
                        break
                if found:
                    continue
            deduped.append(rel)

        return [(rel, os.path.basename(rel), ["file"], "file") for rel in deduped]

    def _dir_meta(self, key: str) -> Optional[dict]:
        """目录虚节点元数据。只返回 labels，虚属性由 _enrich_meta 补充。"""
        if key == ".":
            dir_path = self._project_path
        else:
            dir_path = os.path.join(self._project_path, key)

        if not os.path.isdir(dir_path):
            return None

        return {"_labels": ["dir"]}

    def _file_meta(self, name: str) -> Optional[dict]:
        """未索引文件的元数据。根据扩展名推断具体标签。"""
        physical = os.path.join(self._project_path, name)
        if not os.path.exists(physical):
            return None
        try:
            stat = os.stat(physical)
            ext = os.path.splitext(name)[1].lower()
            label_map = {
                ".db": ["file", "db"], ".sqlite": ["file", "db"], ".sqlite3": ["file", "db"], ".duckdb": ["file", "db"],
                ".csv": ["file", "csv"], ".tsv": ["file", "csv"],
                ".json": ["file", "json"], ".jsonl": ["file", "json"],
                ".yaml": ["file", "yaml"], ".yml": ["file", "yaml"],
                ".xml": ["file", "xml"],
                ".toml": ["file", "toml"],
                ".hcl": ["file", "hcl"],
            }
            labels = label_map.get(ext, ["file"])
            return {
                "_inode": stat.st_ino,
                "_labels": labels,
            }
        except OSError:
            return None

    def _dir_adjacent(self, key: str) -> List[Tuple[str, str, list]]:
        """目录虚节点的邻接子项。"""
        if key == ".":
            dir_path = self._project_path
        else:
            dir_path = os.path.join(self._project_path, key)

        if not os.path.isdir(dir_path):
            return []

        children = []
        try:
            entries = os.listdir(dir_path)
        except OSError:
            return []

        self._ensure_index()

        for entry in entries:
            if entry.startswith('.'):
                continue
            full = os.path.join(dir_path, entry)

            # 忽略图数据库自身
            if self._is_backend_file(full):
                continue

            if os.path.isdir(full):
                child_rel = os.path.relpath(full, self._project_path)
                bare = os.path.basename(child_rel)
                children.append((child_rel, bare, ["dir"]))
            elif os.path.isfile(full):
                try:
                    inode = os.stat(full).st_ino
                except OSError:
                    continue
                ent_id = self._inode_index.get(inode)
                if ent_id:
                    ent_props = self._id_index.get(ent_id, {})
                    ent_name = ent_props.get("name", entry)
                    labels = ent_props.get("_labels", [])
                    children.append((ent_id, ent_name, labels))
                else:
                    child_rel = os.path.relpath(full, self._project_path)
                    children.append((child_rel, entry, ["file"]))

        return children
