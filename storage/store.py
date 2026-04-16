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

    @property
    def index_root(self) -> str:
        """索引文件根目录 (.pontis/_index/)。"""
        return os.path.join(self._pontis_root, "_index")

    # ==================== File Allocation ====================

    def create_file(self, ref: str, meta: dict = None,
                    parent_ref: str = None, edge_type: str = "contains") -> str:
        """在 .pontis/_blobs/ 下分配一个文件路径，注册为 KG 节点。

        只分配路径并创建节点 + 边，不执行文件 I/O。
        返回绝对路径，由调用方自行读写文件内容。

        Args:
            ref: 节点引用 (e.g. "db/event.db::event.id.INT.col.idx")
            meta: 额外元数据
            parent_ref: 父节点 ref，自动创建 contains 边
            edge_type: 边类型

        Returns:
            分配的文件绝对路径
        """
        blob_id = _gen_id()
        blob_dir = os.path.join(self._pontis_root, "_blobs")
        os.makedirs(blob_dir, exist_ok=True)

        blob_relpath = os.path.join(".pontis", "_blobs", f"{blob_id}.bin")
        blob_abs = os.path.join(blob_dir, f"{blob_id}.bin")

        node_meta = {"path": blob_relpath}
        if meta:
            node_meta.update(meta)

        self.set_meta(ref, node_meta)

        if parent_ref:
            self.add_edges([{"from": parent_ref, "to": ref, "type": edge_type}])

        return blob_abs

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

            # 图谱边虚属性：按出边类型自动分组，生成 {edge_type: [target_ref, ...]}
            # 例如表节点的 columns 边 → {"columns": ["db/event.db::event.id.INT.col", ...]}
            # 不覆盖已有字段，完全由图谱结构驱动，无需手动注册
            self._ensure_index()
            for edge in self._outgoing.get(ent_id, []):
                etype = edge["type"]
                to_ref = self._id_index.get(edge["to"])
                if to_ref is None:
                    continue
                if etype not in result:
                    result[etype] = [to_ref]
                elif isinstance(result[etype], list):
                    result[etype].append(to_ref)

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
        """匹配所有节点，合并图谱搜索（Layer 1）和文件系统搜索（Layer 2）。

        Layer 1 — 图谱搜索（fnmatch，递归匹配所有节点名）：
          搜索 .pontis/nodes/ 中的所有显式节点（文件节点 + 实体节点）。
          fnmatch 的 * 匹配任意字符（含 /），因此 *.db 能匹配 db/event.db。
          实体名不含 /，含 / 的 pattern 自动跳过实体节点。
          此层覆盖：已索引的文件 + 所有逻辑实体（.table, .col 等）。

        Layer 2 — 文件系统搜索（glob 语义）：
          使用 glob.glob 扫描物理文件系统，发现未被索引的文件（虚节点）。
          遵循标准 glob 语义：*.db = 仅根目录，**/*.db = 递归。
          此层覆盖：未处理过的文件（无 ent_id，get_meta() 返回纯虚属性）。

        合并策略：Layer 1 结果优先，Layer 2 补充去重。
        如需禁用某层，注释对应代码块即可。
        """
        self._ensure_index()
        has_slash = "/" in pattern
        results = []
        seen = set()

        # ── Layer 1: 图谱搜索（fnmatch，递历） ──────────────────────
        # 匹配所有显式节点：已索引的文件节点 + 逻辑实体节点
        # fnmatch 的 * 匹配任意字符含 /，因此 *.db 匹配任意深度的 .db 节点
        # 实体名不含 /，含 / 的 pattern 自动跳过实体
        for _ent_id, ref in self._id_index.items():
            is_entity = "::" in ref

            if has_slash and is_entity:
                continue

            name = ref.split("::", 1)[1] if is_entity else ref
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(ref, pattern):
                seen.add(ref)
                results.append(ref)

        # ── Layer 2: 文件系统搜索（glob 语义） ──────────────────────
        # 发现未被 extractor 处理过的文件（虚节点）
        # glob 语义：*.db = 根目录，**/*.db = 递归，db/*.db = 指定目录
        # 实体后缀（.table, .col 等）在磁盘上不存在，此层自动返回空
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
