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
        self._adjacent: Dict[str, set] = {}  # node_id → {connected_node_ids}

        # 延迟构建索引（首次访问时）
        self._index_built = False

    # ==================== Properties ====================

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def pontis_exists(self) -> bool:
        return os.path.exists(self._pontis_root)

    @property
    def index_root(self) -> str:
        """索引文件根目录 (.pontis/_index/)。"""
        return os.path.join(self._pontis_root, "_index")

    # ==================== Cache ====================

    def cache_path(self, *parts: str) -> str:
        """返回 .pontis/cache/ 下的绝对路径，自动创建父目录。

        不做文件 I/O，只负责路径分配和目录创建。
        调用方自行读写该路径的文件。

        Args:
            *parts: 路径段，如 "lsh", "ent_a3f2c801.lsh"
        """
        cache_dir = os.path.join(self._pontis_root, "cache")
        path = os.path.join(cache_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def cache_find(self, pattern: str) -> list:
        """在 .pontis/cache/ 下按文件名模式检索，返回匹配的绝对路径列表。

        Args:
            pattern: glob 模式，如 "lsh/*.lsh", "bm25/*", "**/*.pkl"
        """
        import fnmatch as _fnmatch
        cache_dir = os.path.join(self._pontis_root, "cache")
        if not os.path.isdir(cache_dir):
            return []
        results = []
        for root, dirs, files in os.walk(cache_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, cache_dir)
                if _fnmatch.fnmatch(rel, pattern):
                    results.append(full)
        return results

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
        self._adjacent.clear()

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

        # 构建邻接索引
        raw_edges = self._read_edges_raw()
        for e in raw_edges:
            nodes = e.get("nodes", [])
            for nid in nodes:
                for other in nodes:
                    if other != nid:
                        self._adjacent.setdefault(nid, set()).add(other)

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

    def get_meta(self, ref: str, include_props: Optional[List[str]] = None,
                 *, _visiting: Optional[set] = None) -> Optional[dict]:
        """读取 meta + 虚属性。

        有节点 → 存储属性 + 虚属性（虚属性不覆盖已存储字段）
        无节点但文件/目录在磁盘存在 → 纯虚属性
        都不存在或实体无节点 → None

        Args:
            ref: 节点引用
            include_props: 显式指定需要的虚属性列表。
                None = 全部注册的虚属性（默认）
                [] = 仅存储的基础 meta，不计算虚属性
                ["file_size", ...] = 只计算指定的虚属性
            _visiting: (内部) 运行时环路检测的访问状态集，外部调用无需传递。
        """
        # ── 运行时环路检测 ──────────────────────────────────────
        # 防御虚属性函数内部反向调用 get_meta 导致的无限递归。
        # 当检测到环路时，强制降级：仅返回图谱中已存储的基础 meta，跳过虚属性计算。
        # 这是最低成本的递归防护，不需要改变虚属性函数的签名或调用方式。
        if _visiting is not None and ref in _visiting:
            logger.debug(f"Cycle detected in get_meta({ref}), returning base meta only")
            return self._get_stored_meta(ref)

        # 初始化访问状态集
        _visiting = _visiting or set()
        _visiting.add(ref)

        try:
            return self._get_meta_internal(ref, include_props, _visiting)
        finally:
            # 无论成功或异常，退出前解除标记
            _visiting.discard(ref)

    def _get_stored_meta(self, ref: str) -> Optional[dict]:
        """仅读取图谱中已存储的 meta（不计算虚属性，不触发递归）。

        用于环路检测降级和内部需要"纯 meta"的场景。
        """
        path, entity_name = self.resolve_ref(ref)
        ent_id = self._find_id(path, entity_name)

        if ent_id is None:
            return None

        if ent_id in self._meta_cache:
            cached = self._meta_cache[ent_id]
            if cached is None:
                return None
            return {k: v for k, v in cached.items() if k not in _INTERNAL_FIELDS}

        raw = self._read_yaml(self._node_meta_path(ent_id))
        if raw is None:
            self._meta_cache[ent_id] = None
            return None
        self._meta_cache[ent_id] = dict(raw)
        return {k: v for k, v in raw.items() if k not in _INTERNAL_FIELDS}

    def _get_meta_internal(self, ref: str, include_props: Optional[List[str]],
                           _visiting: set) -> Optional[dict]:
        """get_meta 的内部实现，已通过环路检测保护。"""
        from enricher import enrich_meta

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

            # 剥离内部字段
            result = {k: v for k, v in result.items() if k not in _INTERNAL_FIELDS}

            # 图谱边虚属性：按邻接节点实体类型后缀自动分组
            # 例如 event.db → {table: [...], col: [...], view: [...]}
            # 不覆盖已有字段，完全由图谱结构驱动
            self._ensure_index()
            for adj_id in self._adjacent.get(ent_id, set()):
                adj_ref = self._id_index.get(adj_id)
                if adj_ref is None:
                    continue
                # 提取实体类型后缀
                if "::" in adj_ref:
                    entity_part = adj_ref.split("::", 1)[1]
                    suffix = entity_part.rsplit(".", 1)[-1] if "." in entity_part else entity_part
                else:
                    suffix = "file"
                if suffix not in result:
                    result[suffix] = [adj_ref]
                elif isinstance(result[suffix], list):
                    result[suffix].append(adj_ref)

            # 虚属性补充（受 include_props 控制）
            if include_props is None or len(include_props) > 0:
                result = enrich_meta(result, self._project_path, path, entity_name,
                                     include_props=include_props,
                                     store=self, _visiting=_visiting)

            return result

        # 无节点：实体必须存在于图谱
        if entity_name:
            return None

        # 无节点的文件/目录：计算纯虚属性
        if include_props is None or len(include_props) > 0:
            result = enrich_meta({}, self._project_path, path, "",
                                 include_props=include_props,
                                 store=self, _visiting=_visiting)
            return result if result else None

        return None

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

            # 自动边：连接文件和实体（归属关系）
            auto_edge = {
                "a": path,
                "b": ref,
            }
            all_edges = [auto_edge]
            if edges:
                all_edges.extend(edges)
            self.add_edges(all_edges)
        else:
            # 文件/目录节点：自动记录 _inode
            # 优先用 meta["path"]（实际文件路径），fallback 到 ref 路径
            actual = meta.get("path", path) if isinstance(meta, dict) else path
            physical = os.path.join(self._project_path, actual)
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
        """按 pattern 查找节点。

        两阶段设计：
          阶段一（文件发现）：第一段 pattern 通过文件系统 glob 发现文件
          阶段二（边遍历）：后续段通过 :: 沿边遍历实体

        Pattern 语法：
          "*.db"                           → 所有 .db 文件
          "**/*.csv"                       → 递归查找 CSV 文件
          "*.db::*.table"                  → 文件 → 边遍历 → 所有 .table 实体
          "*.db::*.table::*.*.*.col"       → 多跳：文件 → 表 → 列
          "event.db::*"                    → event.db 下所有直连实体

        约束：:: 第一段必须是文件级 pattern（匹配物理文件），
        不能直接用实体后缀（如 "*.table" 无 :: 时返回空）。
        """
        segments = pattern.split("::")

        # 阶段一：文件发现
        refs = self._find_files(segments[0])

        # 阶段二：逐段边遍历
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

    def find_connected(self, ref: str, pattern: str = "*") -> List[str]:
        """从指定节点出发，沿边查找相连节点。"""
        return self._traverse(ref, pattern=pattern)

    def _find_files(self, pattern: str) -> List[str]:
        """文件发现：合并已注册文件节点 + 文件系统 glob。"""
        self._ensure_index()
        results = []
        seen = set()

        # 已注册的文件节点（无 :: 的 ref）
        for _ent_id, ref in self._id_index.items():
            if "::" in ref:
                continue
            if fnmatch.fnmatch(ref, pattern):
                seen.add(ref)
                results.append(ref)

        # 文件系统 glob 补充未注册文件
        for rel_path in self._scan_project_files(pattern):
            if rel_path not in seen:
                seen.add(rel_path)
                results.append(rel_path)

        return results

    def _scan_project_files(self, pattern: str) -> List[str]:
        """Layer 2: 按标准 glob 语义扫描项目目录。

        *.db       → 仅根目录下的 .db 文件
        **/*.db    → 递归所有目录下的 .db 文件
        db/*.db    → db/ 目录下的 .db 文件
        *.table    → 根目录下无 .table 文件（实体不存在于磁盘）

        自动排除 .pontis/ 路径。
        """
        import fnmatch as _fnmatch
        full = os.path.join(self._project_path, pattern)
        # 手动递归 glob，避免 tool_use.glob 命名冲突
        matches = []
        if '**' in pattern:
            base = os.path.join(self._project_path, pattern.split('**')[0] or '')
            if not os.path.isdir(base):
                base = self._project_path
            tail = '**' + pattern.split('**', 1)[1]
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != '.pontis']
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, self._project_path)
                    if _fnmatch.fnmatch(rel, pattern.replace('\\', '/')):
                        matches.append(fp)
        else:
            dir_part = os.path.dirname(full)
            pat_part = os.path.basename(full)
            if os.path.isdir(dir_part):
                for f in os.listdir(dir_part):
                    if _fnmatch.fnmatch(f, pat_part):
                        matches.append(os.path.join(dir_part, f))
        results = []
        for m in matches:
            if not os.path.isfile(m):
                continue
            rel = os.path.relpath(m, self._project_path)
            if '.pontis' in rel.split(os.sep):
                continue
            results.append(rel)
        return sorted(results)

    def _traverse(self, ref: str, pattern: str = "*") -> List[str]:
        """从 ref 出发遍历邻接节点，返回匹配 pattern 的节点。"""
        self._ensure_index()
        path, entity_name = self.resolve_ref(ref)
        node_id = self._path_to_id(path, entity_name)
        if not node_id:
            return []

        results = []
        for adj_id in self._adjacent.get(node_id, set()):
            adj_ref = self._id_index.get(adj_id)
            if adj_ref is None:
                continue
            name = adj_ref.split("::", 1)[1] if "::" in adj_ref else adj_ref
            if fnmatch.fnmatch(name, pattern):
                results.append(adj_ref)

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

    def get_edges(self, node_ref: str = None) -> List[dict]:
        """查询边。返回 ref 格式。

        Args:
            node_ref: 只返回包含此节点的边。None = 全部。
        """
        self._ensure_index()
        raw_edges = self._read_edges_raw()

        node_id = self._resolve_path_to_id(node_ref) if node_ref else None

        result = []
        for e in raw_edges:
            nodes = e.get("nodes", [])
            if node_id and node_id not in nodes:
                continue

            # 转换为 ref 格式
            edge_dict = {
                "nodes": [self._id_index.get(nid, nid) for nid in nodes],
            }
            result.append(edge_dict)

        return result

    def add_edges(self, edges: List[dict]):
        """添加无向边（ref 格式输入，ID 格式存储，去重）。

        边格式：{"a": ref, "b": ref}
        """
        self._ensure_index()
        raw = self._read_edges_raw()
        existing_pairs = {frozenset(e.get("nodes", [])) for e in raw}

        for e in edges:
            a_id = self._resolve_path_to_id(e.get("a", ""))
            b_id = self._resolve_path_to_id(e.get("b", ""))
            if not a_id or not b_id or a_id == b_id:
                continue

            pair = frozenset({a_id, b_id})
            if pair in existing_pairs:
                continue

            entry = {
                "nodes": [a_id, b_id],
            }
            # 可读性：附上 ref
            entry[a_id] = e.get("a", "")
            entry[b_id] = e.get("b", "")

            raw.append(entry)
            existing_pairs.add(pair)

            # 增量更新邻接索引
            self._adjacent.setdefault(a_id, set()).add(b_id)
            self._adjacent.setdefault(b_id, set()).add(a_id)

        self._write_edges_raw(raw)

    def clear_edges(self):
        """清空所有边。"""
        self._write_edges_raw([])
        self._adjacent.clear()

    # ==================== Delete ====================

    def delete_node(self, ref: str) -> str:
        """删除节点及其关联边。

        不变量：删除节点后，不会有悬挂边（所有连接该节点的边一并删除）。

        Returns:
            被删除的节点 ref
        """
        self._ensure_index()

        ent_id = self._resolve_path_to_id(ref)
        if not ent_id or not ent_id.startswith("ent_"):
            return ""

        ref_str = self._id_index.get(ent_id, ent_id)

        # 移除该节点的所有边
        raw = self._read_edges_raw()
        new_raw = [e for e in raw if ent_id not in e.get("nodes", [])]
        self._write_edges_raw(new_raw)

        # 清理邻接索引
        self._adjacent.pop(ent_id, None)
        for adj_set in self._adjacent.values():
            adj_set.discard(ent_id)

        # 删除节点 meta 文件
        meta_dir = os.path.join(self._nodes_root, ent_id)
        if os.path.isdir(meta_dir):
            import shutil
            shutil.rmtree(meta_dir, ignore_errors=True)

        # 清理索引
        self._id_index.pop(ent_id, None)
        self._meta_cache.pop(ent_id, None)
        if ref_str != ent_id:
            if "::" in ref_str:
                path, en = ref_str.split("::", 1)
            else:
                path, en = ref_str, ""
            self._name_index.pop((path, en), None)

        return ref_str

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
