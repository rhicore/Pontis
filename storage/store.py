"""Store — 统一知识图谱存储层

所有节点（文件、目录、实体）使用单一 ref 字符串寻址：
  "db/event.db"              → 文件/目录节点（通过 inode 定位）
  "db/event.db::users.table" → 实体节点（通过路径+名称定位）
  "ent_a3f2c801"             → ID 直接引用

内部存储结构 (.pontis/) — 扁平化，按节点 ID 组织：
  nodes/{ent_id}/_meta.yml   节点元数据
  _edges.yml                 关系边（ID 引用）

文件/目录节点通过 Linux inode 标识，支持跨路径追踪。
"""
import os
import uuid
import fnmatch
import logging
from typing import Dict, Iterator, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# 内部字段：存储在 _meta.yml 中但不暴露给 get_meta()
_INTERNAL_FIELDS = {"_id", "_files", "_inode", "_entity_name"}


def _gen_id() -> str:
    """生成实体 ID: ent_{8位hex}"""
    return f"ent_{uuid.uuid4().hex[:8]}"


def _is_entity_ref(ref: str) -> bool:
    """判断 ref 是否指向实体节点（含 ::）。"""
    return "::" in ref


class Store:
    """统一知识图谱存储层。

    同时服务 extractor 管线和 agent 工具。
    外部使用 ref 字符串寻址，内部用 ent_id 做稳定引用。
    文件/目录节点通过 inode 标识，支持跨路径追踪。

    Args:
        project_path: 项目目录（包含 .pontis/ 的目录）
    """

    def __init__(self, project_path: str):
        self._project_path = os.path.abspath(project_path)
        self._pontis_root = os.path.join(self._project_path, ".pontis")
        self._nodes_root = os.path.join(self._pontis_root, "nodes")

        # 缓存
        self._meta_cache: Dict[str, Optional[dict]] = {}  # ent_id → meta
        self._edges_cache: Optional[List[dict]] = None

        # 索引
        self._id_index: Dict[str, str] = {}               # ent_id → ref
        self._inode_index: Dict[int, str] = {}             # inode → ent_id
        self._name_index: Dict[Tuple[str, str], str] = {}  # (path, entity_name) → ent_id

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
            self._ensure_index()
            ref_str = self._id_index.get(ref)
            if ref_str is None:
                raise KeyError(f"Unknown node ID: {ref}")
            if "::" in ref_str:
                return tuple(ref_str.split("::", 1))
            return (ref_str, "")

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
        """扫描 .pontis/nodes/ 构建 ID/inode/name 索引 + 边索引。"""
        self._id_index.clear()
        self._inode_index.clear()
        self._name_index.clear()
        self._outgoing.clear()
        self._incoming.clear()

        if not os.path.exists(self._nodes_root):
            self._index_built = True
            return

        for entry in os.listdir(self._nodes_root):
            if not entry.startswith("ent_"):
                continue
            meta_file = os.path.join(self._nodes_root, entry, "_meta.yml")
            if not os.path.isfile(meta_file):
                continue

            ent_id = entry
            raw = self._read_yaml(meta_file)
            if raw is None:
                continue

            entity_name = raw.get("_entity_name", "")
            inode = raw.get("_inode")

            # 确定 path
            if entity_name:
                files = raw.get("_files", [])
                path = files[0] if files else ""
            else:
                path = raw.get("path", "")

            ref = self._ref_from_parts(path, entity_name)

            self._id_index[ent_id] = ref
            self._name_index[(path, entity_name)] = ent_id
            if inode is not None:
                self._inode_index[inode] = ent_id

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

    def _register_node(self, ent_id: str, path: str, entity_name: str,
                       inode=None):
        """注册节点到索引。"""
        ref = self._ref_from_parts(path, entity_name)
        self._id_index[ent_id] = ref
        self._name_index[(path, entity_name)] = ent_id
        if inode is not None:
            self._inode_index[inode] = ent_id

    def _find_id(self, path: str, entity_name: str = "") -> Optional[str]:
        """根据 (path, entity_name) 查找 ent_id。

        查找顺序：
        1. name_index 精确匹配
        2. 实体名模糊匹配（无 / 且无 :: 时）
        3. stat → inode 查找（文件路径，inode 是物理文件与图谱的桥梁）
        """
        self._ensure_index()

        # 1. 精确匹配
        hit = self._name_index.get((path, entity_name))
        if hit is not None:
            return hit

        # 2. 无 / 且无 :: 时，按实体名模糊匹配
        if not entity_name and "/" not in path:
            matches = [
                eid for (p, en), eid in self._name_index.items()
                if en == path
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None  # 多匹配需用完整 ref 消歧

        # 3. 文件路径 → inode 查找
        if not entity_name:
            physical = os.path.join(self._project_path, path)
            try:
                inode = os.stat(physical).st_ino
                ent_id = self._inode_index.get(inode)
                if ent_id:
                    return ent_id
                # inode 未在图谱中 → 文件存在但未索引
            except OSError:
                pass  # 文件不存在

        return None

    def _path_to_id(self, path: str, entity_name: str = "") -> Optional[str]:
        """路径 → ID（不尝试 inode，用于已知在索引中的查找）。"""
        self._ensure_index()
        return self._name_index.get((path, entity_name))

    def _id_to_path(self, eid: str) -> Optional[Tuple[str, str]]:
        """ID → (path, entity_name)。"""
        self._ensure_index()
        ref = self._id_index.get(eid)
        if ref is None:
            return None
        if "::" in ref:
            return tuple(ref.split("::", 1))
        return (ref, "")

    # ==================== Internal Path Helpers ====================

    def _node_meta_path(self, ent_id: str) -> str:
        """节点 meta 文件物理路径。"""
        return os.path.join(self._nodes_root, ent_id, "_meta.yml")

    @staticmethod
    def _read_yaml(path: str) -> Optional[dict]:
        """安全读取 YAML 文件。"""
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    # ==================== Meta Read ====================

    def get_meta(self, ref: str) -> Optional[dict]:
        """读取 meta + 虚属性。

        有节点 → 存储属性 + 虚属性（虚属性不覆盖已存储字段）
        无节点但文件/目录在磁盘存在 → 纯虚属性
        都不存在或实体无节点 → None
        """
        from storage.virtual_props import enrich_meta

        path, entity_name = self.resolve_ref(ref)
        ent_id = self._find_id(path, entity_name)

        if ent_id is not None:
            # 有节点：读存储的 meta
            if ent_id in self._meta_cache:
                cached = self._meta_cache[ent_id]
                if cached is None:
                    return None
                result = dict(cached)
            else:
                raw = self._read_yaml(self._node_meta_path(ent_id))
                if raw is None:
                    self._meta_cache[ent_id] = None
                    return None
                result = dict(raw)
                self._meta_cache[ent_id] = dict(raw)

            # 剥离内部字段，补充虚属性
            result = {k: v for k, v in result.items() if k not in _INTERNAL_FIELDS}
            result = enrich_meta(result, self._project_path, path, entity_name)
            return result

        # 无节点：实体必须存在于图谱
        if entity_name:
            return None

        # 无节点的文件/目录：计算纯虚属性
        result = enrich_meta({}, self._project_path, path, "")
        return result if result else None

    def meta_exists(self, ref: str) -> bool:
        """检查 meta 是否存在。"""
        path, entity_name = self.resolve_ref(ref)
        ent_id = self._find_id(path, entity_name)
        return ent_id is not None

    def _read_raw(self, ent_id: str) -> Optional[dict]:
        """按 ent_id 读取原始 meta（含内部字段），不经过缓存。"""
        return self._read_yaml(self._node_meta_path(ent_id))

    # ==================== Meta Write ====================

    def set_meta(self, ref: str, data: dict):
        """合并写入：只更新 data 中的字段，保留已有字段。

        自动维护 _id、_entity_name、_files、_inode。
        """
        path, entity_name = self.resolve_ref(ref)
        ent_id = self._find_id(path, entity_name)

        if ent_id:
            # 已有节点：读取并合并
            existing = self._read_raw(ent_id) or {}
            existing.update(data)
        else:
            # 新节点
            existing = dict(data)
            ent_id = _gen_id()
            existing["_id"] = ent_id

        # 自动维护内部字段
        existing["_entity_name"] = entity_name
        if entity_name and "_files" not in existing:
            existing["_files"] = [path]

        inode = existing.get("_inode")
        self._register_node(ent_id, path, entity_name, inode=inode)

        self._write_meta_file(ent_id, existing)
        self._meta_cache[ent_id] = dict(existing)

    def put_meta(self, ref: str, data: dict):
        """全量写入：替换整个 meta。

        自动维护 _id、_entity_name、_files、_inode。
        """
        path, entity_name = self.resolve_ref(ref)
        ent_id = self._find_id(path, entity_name)

        if ent_id is None:
            ent_id = _gen_id()

        data["_id"] = ent_id
        data["_entity_name"] = entity_name
        if entity_name and "_files" not in data:
            data["_files"] = [path]

        inode = data.get("_inode")
        self._register_node(ent_id, path, entity_name, inode=inode)

        self._write_meta_file(ent_id, data)
        self._meta_cache[ent_id] = dict(data)

    def _write_meta_file(self, ent_id: str, data: dict):
        """写入 _meta.yml 到磁盘。"""
        mp = self._node_meta_path(ent_id)
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(mp, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # ==================== Node Operations ====================

    def create_node(self, ref: str, *, meta: Optional[dict] = None,
                    edges: Optional[List[dict]] = None,
                    files: Optional[List[str]] = None):
        """创建节点。

        ref 含 :: → 实体节点（创建 meta + contains 边 + 用户边）
        ref 不含 :: → 文件/目录节点（写 meta + 自动记录 _inode）

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
            # 文件/目录节点：自动记录 _inode
            physical = os.path.join(self._project_path, path)
            if os.path.exists(physical):
                try:
                    meta["_inode"] = os.stat(physical).st_ino
                except OSError:
                    pass
            self.put_meta(ref, meta)
            if edges:
                self.add_edges(edges)

    def node_exists(self, ref: str) -> bool:
        """检查节点是否存在。"""
        path, entity_name = self.resolve_ref(ref)
        ent_id = self._find_id(path, entity_name)
        return ent_id is not None

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

        # 多段：逐段遍历（每跳去重）
        refs = self._match_all_nodes(segments[0])
        for seg in segments[1:]:
            next_refs = []
            seen = set()
            for r in refs:
                for hit in self._traverse(r, pattern=seg):
                    if hit not in seen:
                        seen.add(hit)
                        next_refs.append(hit)
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
        """匹配所有节点（文件 + 实体），基于内存索引。

        含 / 的 pattern 只匹配文件节点（实体名不含 /）。
        否则同时匹配文件路径和实体名。
        """
        self._ensure_index()
        has_slash = "/" in pattern
        results = []
        for ent_id, ref in self._id_index.items():
            is_entity = "::" in ref

            # 含 / 的 pattern 只可能是文件路径
            if has_slash and is_entity:
                continue

            name = ref.split("::", 1)[1] if is_entity else ref
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(ref, pattern):
                results.append(ref)
        return results

    def _traverse(self, ref: str, edge_type: str = None,
                  pattern: str = "*") -> List[str]:
        """从 ref 出发沿边遍历（双向），返回匹配 pattern 的目标节点。"""
        self._ensure_index()
        path, entity_name = self.resolve_ref(ref)
        node_id = self._path_to_id(path, entity_name)
        if not node_id:
            return []

        seen = set()
        results = []

        def _try_match(target_id: str):
            if target_id in seen:
                return
            seen.add(target_id)
            to_ref = self._id_index.get(target_id)
            if to_ref is None:
                return
            name = to_ref.split("::", 1)[1] if "::" in to_ref else to_ref
            if fnmatch.fnmatch(name, pattern):
                results.append(to_ref)

        # 出边
        for edge in self._outgoing.get(node_id, []):
            if edge_type and edge["type"] != edge_type:
                continue
            _try_match(edge["to"])

        # 入边
        for edge in self._incoming.get(node_id, []):
            if edge_type and edge["type"] != edge_type:
                continue
            _try_match(edge["from"])

        return results

    def walk_metas(self) -> Iterator[Tuple[str, dict]]:
        """遍历所有 meta（含虚属性），yield (ref, meta)。"""
        self._ensure_index()

        for ent_id, ref in self._id_index.items():
            meta = self.get_meta(ref)
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
        """查询边，参数和返回值都用 ref 格式。"""
        self._ensure_index()
        raw_edges = self._read_edges_raw()

        result = []
        for e in raw_edges:
            e_from = self._resolve_edge_ref(e.get("from", ""))
            e_to = self._resolve_edge_ref(e.get("to", ""))
            etype = e.get("type", "")

            if from_ref and e_from != from_ref:
                continue
            if edge_type and etype != edge_type:
                continue
            if to_ref and e_to != to_ref:
                continue

            result.append({
                "from": e_from,
                "type": etype,
                "to": e_to,
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
            return self._id_index.get(ref, ref)
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
