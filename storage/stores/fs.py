"""FSStore — 文件系统 Store 实现（POSIX / Windows）。

封装 .pontis YAML 持久化、文件系统发现、目录虚节点、虚属性计算。
"""
import os
import shutil
import fnmatch
import logging
from typing import Dict, List, Optional, Tuple

import yaml

from storage.store import Store

logger = logging.getLogger(__name__)


class FSStore(Store):
    """文件系统 Store（POSIX / Windows）。

    Args:
        project_path: 项目目录（包含 .pontis/ 的目录）
    """

    def __init__(self, project_path: str):
        super().__init__()
        self._project_path = os.path.abspath(project_path)
        self._pontis_root = os.path.join(self._project_path, ".pontis")
        self._nodes_root = os.path.join(self._pontis_root, "nodes")
        self._dirs_cache: Dict[str, str] = {}  # full_rel_path → bare_name

    # ==================== Properties ====================

    # ==================== Legacy Compatibility ====================

    def find_nodes(self, pattern: str) -> List[str]:
        """兼容旧 extractor — 也搜索虚文件。"""
        results = super().find_nodes(pattern)
        seen = set(results)

        # 对无 label 的单段模式，也搜索虚文件
        _, label_filter = self._split_pattern_label(pattern)
        if not label_filter:
            for vkey, vname, _, vtype in self.discover_virtual(pattern):
                if vtype == "file" and vkey not in seen:
                    seen.add(vkey)
                    results.append(vkey)
        return results

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

    # ==================== Persistence ====================

    def _scan_entities(self) -> List[Tuple[str, dict]]:
        if not os.path.exists(self._nodes_root):
            return []
        results = []
        for entry in os.listdir(self._nodes_root):
            if not entry.startswith("ent_"):
                continue
            meta_file = os.path.join(self._nodes_root, entry, "_meta.yml")
            if not os.path.isfile(meta_file):
                continue
            raw = self._read_yaml(meta_file)
            if raw is not None:
                results.append((entry, raw))
        return results

    def _read_entity_meta(self, ent_id: str) -> Optional[dict]:
        return self._read_yaml(os.path.join(self._nodes_root, ent_id, "_meta.yml"))

    def _write_entity_meta(self, ent_id: str, data: dict):
        mp = os.path.join(self._nodes_root, ent_id, "_meta.yml")
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(mp, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    def _delete_entity_storage(self, ent_id: str):
        meta_dir = os.path.join(self._nodes_root, ent_id)
        if os.path.isdir(meta_dir):
            shutil.rmtree(meta_dir, ignore_errors=True)

    def _read_edges_storage(self) -> List[dict]:
        ep = os.path.join(self._pontis_root, "_edges.yml")
        if not os.path.exists(ep):
            return []
        try:
            with open(ep, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            return data.get("edges", [])
        except Exception:
            return []

    def _write_edges_storage(self, edges: List[dict]):
        os.makedirs(self._pontis_root, exist_ok=True)
        ep = os.path.join(self._pontis_root, "_edges.yml")
        with open(ep, 'w', encoding='utf-8') as f:
            yaml.dump({"edges": edges}, f,
                      default_flow_style=False, allow_unicode=True)

    def _ensure_storage(self):
        os.makedirs(self._nodes_root, exist_ok=True)

    def _read_yaml(self, path: str) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    def _record_read_timestamp(self, ent_id: str):
        meta_path = os.path.join(self._nodes_root, ent_id, "_meta.yml")
        try:
            self._read_timestamps[ent_id] = os.path.getmtime(meta_path)
        except OSError:
            pass

    def _check_stale(self, ent_id: str) -> Optional[str]:
        if ent_id not in self._read_timestamps:
            return None
        meta_path = os.path.join(self._nodes_root, ent_id, "_meta.yml")
        try:
            current_mtime = os.path.getmtime(meta_path)
        except OSError:
            return None
        if current_mtime > self._read_timestamps[ent_id]:
            return "Entity was modified after read. Read it again before updating."
        return None

    # ==================== File Identity ====================

    def _detect_file_identity(self, meta: dict, ename: str) -> Optional[int]:
        physical = os.path.join(self._project_path, ename)
        try:
            if os.path.exists(physical) and os.path.isfile(physical):
                return os.stat(physical).st_ino
        except OSError:
            pass
        return None

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
        return self.create_node(ref, meta=meta, labels=labels)

    # ==================== Virtual Property Enrichment ====================

    def _enrich_meta(self, ent_id: str, meta: dict,
                     include_props=None) -> dict:
        """为实体补充虚属性。"""
        from storage.enricher import enrich_meta

        # 推断 file_rel_path 和 entity_path
        entity_labels = meta.get("_labels", [])
        ename = self._id_index.get(ent_id, "")

        # 从邻接文件实体获取 file_rel_path
        file_rel_path = ""
        self._ensure_index()
        for adj_id in self._adjacent.get(ent_id, set()):
            adj_labels = self._get_labels_by_id(adj_id)
            if any(l.startswith("file") for l in adj_labels):
                adj_meta = self._meta_cache.get(adj_id, {})
                file_rel_path = adj_meta.get("path", self._id_index.get(adj_id, ""))
                break
        if not file_rel_path:
            file_rel_path = meta.get("path", "")
        entity_path = "" if any(l.startswith("file") for l in entity_labels) else ename

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

        if '**' in pattern:
            base = os.path.join(project_path, pattern.split('**')[0] or '')
            if not os.path.isdir(base):
                base = project_path
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != '.pontis']
                for f in files:
                    fp = os.path.join(root, f)
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
                        if os.path.isfile(fp):
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
                ".db": "file/db", ".sqlite": "file/db", ".sqlite3": "file/db", ".duckdb": "file/db",
                ".csv": "file/csv", ".tsv": "file/csv",
                ".json": "file/json", ".jsonl": "file/json",
                ".yaml": "file/yaml", ".yml": "file/yaml",
                ".xml": "file/xml",
                ".toml": "file/toml",
                ".hcl": "file/hcl",
            }
            labels = [label_map.get(ext, "file")]
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
                    ent_name = self._id_index.get(ent_id, entry)
                    labels = self._get_labels_by_id(ent_id)
                    children.append((ent_id, ent_name, labels))
                else:
                    child_rel = os.path.relpath(full, self._project_path)
                    children.append((child_rel, entry, ["file"]))

        return children
