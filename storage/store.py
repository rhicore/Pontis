"""Store — 统一知识图谱存储层

所有节点（文件、目录、实体）使用单一 ref 字符串寻址：
  "db/event.db"              → 文件/目录节点
  "db/event.db::users.table" → 实体节点（:: 为边遍历操作符）
  "ent_a3f2c801"             → ID 直接引用

内部维护 _id 索引和 _files 关联，不暴露给调用方。

物理存储结构 (.pontis/) — 内部实现细节，调用方不可见：
  <path>/_meta.yml              文件级元数据
  <path>/_entity/<name>/_meta.yml  实体元数据
  _edges.yml                     关系边（ID 引用）
"""
import os
import uuid
import fnmatch
import logging
from typing import Dict, Iterator, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# 内部字段：存储在 _meta.yml 中但不暴露给 get_meta()
_INTERNAL_FIELDS = {"_id", "_files"}


def _gen_id() -> str:
    """生成实体 ID: ent_{8位hex}"""
    return f"ent_{uuid.uuid4().hex[:8]}"


def _is_entity_ref(ref: str) -> bool:
    """判断 ref 是否指向实体节点（含 ::）。"""
    return "::" in ref


class Store:
    """统一知识图谱存储层。

    同时服务 extractor 管线和 agent 工具。
    外部使用 ref 字符串寻址，内部用 _id 做稳定引用。

    Args:
        project_path: 项目目录（包含 .pontis/ 的目录）
    """

    def __init__(self, project_path: str):
        self._project_path = os.path.abspath(project_path)
        self._pontis_root = os.path.join(self._project_path, ".pontis")

        # 缓存
        self._meta_cache: Dict[str, Optional[dict]] = {}
        self._edges_cache: Optional[List[dict]] = None

        # ID 索引：path ↔ id 双向映射
        self._id_index: Dict[str, Tuple[str, str]] = {}      # id → (path, entity_name)
        self._path_index: Dict[Tuple[str, str], str] = {}    # (path, entity_name) → id

        # 边索引：用于 :: 遍历
        self._outgoing: Dict[str, List[dict]] = {}  # node_id → [{type, to}, ...]
        self._incoming: Dict[str, List[dict]] = {}  # node_id → [{type, from}, ...]

        # 延迟构建索引（首次访问时）
        self._index_built = False

    # ==================== Properties ====================

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def pontis_exists(self) -> bool:
        return os.path.exists(self._pontis_root)

    # ==================== Ref Resolution ====================

    def resolve_ref(self, ref: str) -> Tuple[str, str]:
        """将 ref 字符串解析为内部 (path, entity_name)。

        三种格式：
          "db/event.db"              → ("db/event.db", "")
          "db/event.db::users.table" → ("db/event.db", "users.table")
          "ent_a3f2c801"             → ID 查表 → (path, entity_name)
        """
        if ref.startswith("ent_"):
            pair = self._id_to_path(ref)
            if pair is None:
                raise KeyError(f"Unknown node ID: {ref}")
            return pair

        if "::" in ref:
            path, entity_name = ref.split("::", 1)
            return (path, entity_name)

        return (ref, "")

    @staticmethod
    def _ref_from_parts(path: str, entity_name: str = "") -> str:
        """从 (path, entity_name) 构造 ref 字符串。"""
        return f"{path}::{entity_name}" if entity_name else path

    # ==================== Index ====================

    def _ensure_index(self):
        if self._index_built:
            return
        self._build_index()

    def _build_index(self):
        """扫描 .pontis/ 构建 ID 索引 + 边索引。"""
        self._id_index.clear()
        self._path_index.clear()
        self._outgoing.clear()
        self._incoming.clear()

        if not os.path.exists(self._pontis_root):
            self._index_built = True
            return

        for root, dirs, files in os.walk(self._pontis_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            if "_meta.yml" not in files:
                continue
            rel = os.path.relpath(root, self._pontis_root)
            if rel == ".":
                continue
            raw = self._read_raw_by_rel(rel)
            if raw is None:
                continue
            path, entity_name = self._parse_rel(rel)
            eid = raw.get("_id")
            if eid:
                self._id_index[eid] = (path, entity_name)
                self._path_index[(path, entity_name)] = eid

        # 构建边索引
        raw_edges = self._read_edges_raw()
        for e in raw_edges:
            from_id = e.get("from", "")
            to_id = e.get("to", "")
            etype = e.get("type", "")
            self._outgoing.setdefault(from_id, []).append(
                {"type": etype, "to": to_id}
            )
            self._incoming.setdefault(to_id, []).append(
                {"type": etype, "from": from_id}
            )

        self._index_built = True

    def _register_id(self, eid: str, path: str, entity_name: str):
        """注册新实体 ID 到索引。"""
        self._id_index[eid] = (path, entity_name)
        self._path_index[(path, entity_name)] = eid

    def _path_to_id(self, path: str, entity_name: str = "") -> Optional[str]:
        """路径 → ID。"""
        self._ensure_index()
        return self._path_index.get((path, entity_name))

    def _id_to_path(self, eid: str) -> Optional[Tuple[str, str]]:
        """ID → (path, entity_name)。"""
        self._ensure_index()
        return self._id_index.get(eid)

    @staticmethod
    def _parse_rel(rel: str) -> Tuple[str, str]:
        """将 .pontis/ 内的相对路径解析为 (path, entity_name)。"""
        parts = rel.replace(os.sep, "/").split("/")
        try:
            idx = parts.index("_entity")
            path = "/".join(parts[:idx])
            entity_name = "/".join(parts[idx + 1:])
            return (path, entity_name)
        except ValueError:
            return (rel.replace(os.sep, "/"), "")

    # ==================== Internal Path Helpers ====================

    def _meta_path(self, path: str, entity_name: str = "") -> str:
        """解析 meta 文件物理路径。"""
        if entity_name:
            return os.path.join(
                self._pontis_root, path, "_entity", entity_name, "_meta.yml"
            )
        return os.path.join(self._pontis_root, path, "_meta.yml")

    def _entity_dir(self, path: str, entity_name: str) -> str:
        """实体目录路径。"""
        return os.path.join(self._pontis_root, path, "_entity", entity_name)

    @staticmethod
    def _cache_key(path: str, entity_name: str = "") -> str:
        return f"{path}::{entity_name}" if entity_name else path

    # ==================== Meta Read ====================

    def get_meta(self, ref: str, *, enrich: bool = False) -> Optional[dict]:
        """读取 meta，自动剥离内部字段。

        Args:
            ref: 节点引用（路径、路径::实体、ID）
            enrich: True 时补充虚属性（agent 工具用）
        """
        path, entity_name = self.resolve_ref(ref)
        key = self._cache_key(path, entity_name)

        if key in self._meta_cache:
            cached = self._meta_cache[key]
            if cached is None:
                return None
            result = dict(cached)
        else:
            raw = self._read_raw(path, entity_name)
            if raw is None:
                self._meta_cache[key] = None
                return None
            result = dict(raw)
            self._meta_cache[key] = dict(raw)

        # 虚属性补充
        if enrich:
            from storage.virtual_props import enrich_meta
            result = enrich_meta(result, self._project_path, path, entity_name)

        # 剥离内部字段
        return {k: v for k, v in result.items() if k not in _INTERNAL_FIELDS}

    def meta_exists(self, ref: str) -> bool:
        """检查 meta 是否存在。"""
        path, entity_name = self.resolve_ref(ref)
        return os.path.exists(self._meta_path(path, entity_name))

    def _read_raw(self, path: str, entity_name: str = "") -> Optional[dict]:
        """读取原始 meta（含内部字段），不经过缓存。"""
        mp = self._meta_path(path, entity_name)
        if not os.path.exists(mp):
            return None
        try:
            with open(mp, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    def _read_raw_by_rel(self, rel: str) -> Optional[dict]:
        """按 .pontis/ 内相对路径读取原始 meta。"""
        mp = os.path.join(self._pontis_root, rel, "_meta.yml")
        if not os.path.exists(mp):
            return None
        try:
            with open(mp, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    # ==================== Meta Write ====================

    def set_meta(self, ref: str, data: dict):
        """合并写入：只更新 data 中的字段，保留已有字段。

        自动维护 _id（新节点）和 _files。
        """
        path, entity_name = self.resolve_ref(ref)
        existing = self._read_raw(path, entity_name) or {}
        existing.update(data)

        # 自动补充 _id
        if "_id" not in existing:
            eid = _gen_id()
            existing["_id"] = eid
            self._register_id(eid, path, entity_name)

        # 自动补充 _files（仅实体）
        if entity_name and "_files" not in existing:
            existing["_files"] = [path]

        self._write_meta_file(path, entity_name, existing)
        self._meta_cache[self._cache_key(path, entity_name)] = dict(existing)

    def put_meta(self, ref: str, data: dict):
        """全量写入：替换整个 meta。

        自动维护 _id 和 _files。extractor 初始化用。
        """
        path, entity_name = self.resolve_ref(ref)

        # 自动补充 _id
        if "_id" not in data:
            eid = _gen_id()
            data["_id"] = eid
            self._register_id(eid, path, entity_name)

        # 自动补充 _files（仅实体）
        if entity_name and "_files" not in data:
            data["_files"] = [path]

        self._write_meta_file(path, entity_name, data)
        self._meta_cache[self._cache_key(path, entity_name)] = dict(data)

    def _write_meta_file(self, path: str, entity_name: str, data: dict):
        """写入 _meta.yml 到磁盘。"""
        mp = self._meta_path(path, entity_name)
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(mp, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # ==================== Node Operations ====================

    def create_node(self, ref: str, *, meta: Optional[dict] = None,
                    edges: Optional[List[dict]] = None,
                    files: Optional[List[str]] = None):
        """创建节点。

        ref 含 :: → 实体节点（创建目录 + meta + contains 边 + 用户边）
        ref 不含 :: → 文件/目录节点（只写 meta）

        Args:
            ref: 节点引用
            meta: 初始元数据
            edges: 关系边列表
            files: 关联文件列表（仅实体，默认 [path]）
        """
        path, entity_name = self.resolve_ref(ref)
        meta = meta or {}

        if entity_name:
            # 实体节点
            edir = self._entity_dir(path, entity_name)
            os.makedirs(edir, exist_ok=True)

            if files:
                meta["_files"] = files
            else:
                meta.setdefault("_files", [path])

            self.put_meta(ref, meta)

            # 自动 contains 边 + 用户边
            contains_edge = {
                "from": path,
                "type": "contains",
                "to": ref,
            }
            all_edges = [contains_edge]
            if edges:
                all_edges.extend(edges)
            self.add_edges(all_edges)
        else:
            # 文件/目录节点
            self.put_meta(ref, meta)
            if edges:
                self.add_edges(edges)

    def node_exists(self, ref: str) -> bool:
        """检查节点是否存在。"""
        path, entity_name = self.resolve_ref(ref)
        if entity_name:
            return os.path.isdir(self._entity_dir(path, entity_name))
        return os.path.exists(self._meta_path(path))

    # ==================== Node Discovery ====================

    def find_nodes(self, pattern: str) -> List[str]:
        """按 pattern 查找节点，返回 ref 字符串列表。

        Pattern 语法：
          "*.db"              → 匹配所有 .db 文件节点
          "*.table"           → 匹配所有 .table 实体节点（跨文件搜索）
          "*.db::*.table"     → DB 文件节点 → 遍历边 → 匹配 .table 实体
          "event.db::*"       → event.db 下所有相连实体
        """
        segments = pattern.split("::")

        if len(segments) == 1:
            return self._match_all_nodes(segments[0])

        # 多段：逐段遍历
        refs = self._match_all_nodes(segments[0])
        for seg in segments[1:]:
            next_refs = []
            for ref in refs:
                next_refs.extend(self._traverse(ref, pattern=seg))
            refs = next_refs
        return refs

    def find_connected(self, ref: str, edge_type: str = None,
                       pattern: str = "*") -> List[str]:
        """从指定节点出发，沿边查找相连节点。

        Args:
            ref: 起始节点引用
            edge_type: 只沿此类型的边遍历（None = 所有类型）
            pattern: 过滤目标节点名
        """
        return self._traverse(ref, edge_type=edge_type, pattern=pattern)

    def _match_all_nodes(self, pattern: str) -> List[str]:
        """匹配所有节点（文件 + 实体），不暴露 _entity/。"""
        if not os.path.exists(self._pontis_root):
            return []

        results = []
        for root, dirs, files in os.walk(self._pontis_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            if "_meta.yml" not in files:
                continue
            rel = os.path.relpath(root, self._pontis_root)
            if rel == ".":
                continue
            path, entity_name = self._parse_rel(rel)
            ref = self._ref_from_parts(path, entity_name)

            # 用节点名匹配（文件用 path，实体用 entity_name）
            name = entity_name if entity_name else path
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(ref, pattern):
                results.append(ref)

        return results

    def _traverse(self, ref: str, edge_type: str = None,
                  pattern: str = "*") -> List[str]:
        """从 ref 出发沿边遍历，返回匹配 pattern 的目标节点。"""
        self._ensure_index()
        path, entity_name = self.resolve_ref(ref)
        node_id = self._path_to_id(path, entity_name)
        if not node_id:
            return []

        results = []
        for edge in self._outgoing.get(node_id, []):
            if edge_type and edge["type"] != edge_type:
                continue
            to_id = edge["to"]
            to_pair = self._id_to_path(to_id)
            if to_pair is None:
                continue
            to_path, to_ename = to_pair
            to_ref = self._ref_from_parts(to_path, to_ename)
            # 匹配节点名
            name = to_ename if to_ename else to_path
            if fnmatch.fnmatch(name, pattern):
                results.append(to_ref)

        return results

    def walk_metas(self, *, enrich: bool = False) -> Iterator[Tuple[str, dict]]:
        """遍历所有 meta，yield (ref, meta)。"""
        if not os.path.exists(self._pontis_root):
            return

        for root, dirs, files in os.walk(self._pontis_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            if "_meta.yml" not in files:
                continue
            rel_dir = os.path.relpath(root, self._pontis_root)
            if rel_dir == ".":
                continue
            path, entity_name = self._parse_rel(rel_dir)
            ref = self._ref_from_parts(path, entity_name)
            meta = self.get_meta(ref, enrich=enrich)
            if meta is not None:
                yield (ref, meta)

    # ==================== Edges ====================

    def _edges_path(self) -> str:
        return os.path.join(self._pontis_root, "_edges.yml")

    def _read_edges_raw(self) -> List[dict]:
        """读取原始边数据（含 ID）。"""
        if self._edges_cache is not None:
            return self._edges_cache

        ep = self._edges_path()
        if not os.path.exists(ep):
            self._edges_cache = []
            return []

        try:
            with open(ep, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self._edges_cache = data.get("edges", [])
            return self._edges_cache
        except Exception:
            self._edges_cache = []
            return []

    def _write_edges_raw(self, edges: List[dict]):
        """写入原始边数据。"""
        os.makedirs(self._pontis_root, exist_ok=True)
        with open(self._edges_path(), 'w', encoding='utf-8') as f:
            yaml.dump({"edges": edges}, f,
                      default_flow_style=False, allow_unicode=True)
        self._edges_cache = edges

    def get_edges(self, from_ref: str = None, edge_type: str = None,
                  to_ref: str = None) -> List[dict]:
        """查询边，参数和返回值都用路径格式。"""
        self._ensure_index()
        raw_edges = self._read_edges_raw()

        result = []
        for e in raw_edges:
            from_path = self._resolve_edge_ref(e.get("from", ""))
            to_path = self._resolve_edge_ref(e.get("to", ""))
            etype = e.get("type", "")

            if from_ref and from_path != from_ref:
                continue
            if edge_type and etype != edge_type:
                continue
            if to_ref and to_path != to_ref:
                continue

            result.append({
                "from": from_path,
                "type": etype,
                "to": to_path,
            })

        return result

    def add_edges(self, edges: List[dict]):
        """添加边（路径格式输入，ID 格式存储，去重）。"""
        self._ensure_index()
        raw = self._read_edges_raw()
        existing_keys = {(e.get("from", ""), e.get("type", ""), e.get("to", ""))
                         for e in raw}

        for e in edges:
            from_id = self._resolve_path_to_id(e.get("from", ""))
            to_id = self._resolve_path_to_id(e.get("to", ""))
            etype = e.get("type", "")

            key = (from_id, etype, to_id)
            if key in existing_keys:
                continue

            entry = {
                "from": from_id,
                "from_path": e.get("from", ""),
                "type": etype,
                "to": to_id,
                "to_path": e.get("to", ""),
            }
            raw.append(entry)
            existing_keys.add(key)

            # 增量更新边索引
            self._outgoing.setdefault(from_id, []).append(
                {"type": etype, "to": to_id}
            )
            self._incoming.setdefault(to_id, []).append(
                {"type": etype, "from": from_id}
            )

        self._write_edges_raw(raw)

    def clear_edges(self):
        """清空所有边。"""
        self._write_edges_raw([])
        self._outgoing.clear()
        self._incoming.clear()

    def _resolve_edge_ref(self, ref: str) -> str:
        """边引用 → 路径格式。如果是 ID 就转换，否则原样返回。"""
        if ref.startswith("ent_"):
            pair = self._id_to_path(ref)
            if pair:
                path, entity_name = pair
                return f"{path}::{entity_name}" if entity_name else path
        return ref

    def _resolve_path_to_id(self, ref: str) -> str:
        """路径格式 → ID。如果已是 ID 就原样返回。"""
        if ref.startswith("ent_"):
            return ref
        # 解析 "path::entity"
        if "::" in ref:
            path, entity_name = ref.split("::", 1)
        else:
            path, entity_name = ref, ""
        eid = self._path_to_id(path, entity_name)
        return eid if eid else ref
