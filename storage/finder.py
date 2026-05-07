"""Finder — Cypher 风格 URN 查询引擎。

负责 URN 解析、层级标签匹配、图遍历、跨 store 聚合。
"""
import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Dict, Iterator, List, Optional, Set, Tuple

from storage.config import StoreConfig
from storage import stores

import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  URN 解析结果
# ═══════════════════════════════════════════════════════════

@dataclass
class Segment:
    project: Optional[str] = None
    pattern: str = "*"
    labels_and: List[str] = field(default_factory=list)
    labels_or: List[str] = field(default_factory=list)


@dataclass
class URNParsed:
    segments: List[Segment] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
#  URN 解析器
# ═══════════════════════════════════════════════════════════

_MULTI_SEG_RE = re.compile(r"\(([^)]+)\)")
_PROJECT_RE = re.compile(r"^(\w[\w.-]*)://")
_LABEL_RE = re.compile(r"[:|]([^:|]+)")


def parse_urn(urn: str) -> URNParsed:
    """解析 URN 字符串为 Segment 列表。

    支持两种形式：
    - 单段：`[Project://]Pattern[:Label1[:Label2[|Label3]]]`
    - 多段：`(Seg1)--(Seg2)--(Seg3)`
    """
    # 检测多段格式
    bracket_segments = _MULTI_SEG_RE.findall(urn)
    if bracket_segments:
        return URNParsed(segments=[_parse_segment(s) for s in bracket_segments])

    # 单段
    return URNParsed(segments=[_parse_segment(urn)])


def _parse_segment(text: str) -> Segment:
    """解析单个段：`[Project://]Pattern[:Label1[:Label2[|Label3]]]`"""
    text = text.strip()
    project = None

    # 提取 Project://
    m = _PROJECT_RE.match(text)
    if m:
        project = m.group(1)
        text = text[m.end():]

    # 分离 pattern 和 label 过滤
    # 找第一个 : 后面紧跟非 glob 字符的部分
    parts = _split_pattern_labels(text)
    pattern = parts[0] if parts else "*"

    labels_and = []
    labels_or = []
    for label in parts[1:]:
        if "|" in label:
            labels_or.extend(label.split("|"))
        else:
            labels_and.append(label)

    return Segment(project=project, pattern=pattern or "*",
                   labels_and=labels_and, labels_or=labels_or)


def _split_pattern_labels(text: str) -> List[str]:
    """将 `Pattern:Label1:Label2|Label3` 拆分为 [Pattern, Label1, Label2|Label3]。

    关键：`:` 后的部分如果不含 glob 字符，则视为标签分隔符。
    `:` 前的部分可以是任意 glob 模式（包括 `*`）。
    """
    parts = []
    current = []
    found_boundary = False

    for i, ch in enumerate(text):
        if ch == ":" and not found_boundary and current:
            # Check if remainder after ':' looks like a label
            remaining = text[i + 1:]
            next_colon = remaining.find(":")
            candidate = remaining[:next_colon] if next_colon >= 0 else remaining

            if candidate and not any(c in candidate for c in "*?[]"):
                parts.append("".join(current))
                current = []
                found_boundary = True
            else:
                current.append(ch)
        elif ch == ":" and found_boundary:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)

    remainder = "".join(current)
    if remainder:
        parts.append(remainder)

    return parts


# ═══════════════════════════════════════════════════════════
#  Finder 核心
# ═══════════════════════════════════════════════════════════

class Finder:
    def __init__(self, config: StoreConfig):
        self._config = config
        self._stores: Dict[str, "Store"] = {}
        self._mounted: Set[str] = set()

    # ── Store 管理 ──

    def register_store(self, project: str, store: "Store"):
        self._stores[project] = store
        self._mounted.add(project)

    def get_store(self, project: str) -> Optional["Store"]:
        if project not in self._stores:
            path = self._config.resolve_path(project)
            if path:
                backend = self._config.resolve_backend(project)
                self._stores[project] = stores.create_store(backend, path)
                self._mounted.add(project)
        return self._stores.get(project)

    def get_default_store(self) -> Optional["Store"]:
        dp = self._config.default_project()
        if dp:
            return self.get_store(dp)
        return None

    def all_stores(self) -> List[Tuple[str, "Store"]]:
        return list(self._stores.items())

    def is_mounted(self, project: str) -> bool:
        return project in self._mounted

    def query(self, cypher: str) -> list:
        """Cypher 查询入口。"""
        from storage.cypher import parse_cypher, CypherExecutor
        store = self.get_default_store()
        if not store:
            return []
        executor = CypherExecutor(store)
        return executor.execute(parse_cypher(cypher))

    # ── 核心查询 ──

    def find(self, urn: str) -> List[Tuple[str, List[str]]]:
        """Cypher 风格 URN 查询，返回 [(entity_name, labels), ...]"""
        parsed = parse_urn(urn)
        if not parsed.segments:
            return []

        if len(parsed.segments) == 1:
            return self._single_segment_find(parsed.segments[0])

        return self._traverse_find(parsed.segments)

    def find_in_project(self, project: str, urn: str) -> List[Tuple[str, List[str]]]:
        """在指定 project 内查询。"""
        store = self.get_store(project)
        if not store:
            return []
        parsed = parse_urn(urn)
        if not parsed.segments:
            return []

        if len(parsed.segments) == 1:
            return self._match_segment_in_store(store, parsed.segments[0])

        return self._traverse_in_store(store, parsed.segments)

    # ── 路由 ──

    def route_create(self, entity_name: str) -> Optional["Store"]:
        """根据 config routing 规则返回目标 Store。"""
        target_project = self._config.route_entity(entity_name)
        if target_project:
            return self.get_store(target_project)
        return self.get_default_store()

    # ── 遍历 ──

    def walk_all(self) -> Iterator[Tuple[str, str, dict]]:
        """遍历所有 project，yield (project, entity_name, meta)。"""
        for project, store in self._stores.items():
            for name, labels in store.list_all():
                meta = store.get_meta(name)
                if meta:
                    yield project, name, meta

    # ═══════════════════════════════════════════════════════
    #  内部实现
    # ═══════════════════════════════════════════════════════

    def _single_segment_find(self, seg: Segment) -> List[Tuple[str, List[str]]]:
        """单段查询：确定搜索范围，匹配 pattern + labels。"""
        if seg.project:
            store = self.get_store(seg.project)
            if not store:
                return []
            return self._match_segment_in_store(store, seg)

        # 未指定 project → 搜索所有 store
        results = []
        seen = set()
        for project, store in self.all_stores():
            for name, labels in self._match_segment_in_store(store, seg):
                key = f"{project}:{name}"
                if key not in seen:
                    seen.add(key)
                    results.append((name, labels))
        return results

    def _traverse_find(self, segments: List[Segment]) -> List[Tuple[str, List[str]]]:
        """多段遍历查询。"""
        first = segments[0]

        # 确定起始 store
        if first.project:
            store = self.get_store(first.project)
            if not store:
                return []
        else:
            store = self.get_default_store()
            if not store:
                return []

        return self._traverse_in_store(store, segments)

    def _traverse_in_store(self, store: "Store",
                           segments: List[Segment]) -> List[Tuple[str, List[str]]]:
        """在单个 store 内做多段遍历。"""
        first = segments[0]

        # 第一段：匹配种子节点
        current_ids = set()
        for name, labels in store.list_all():
            if fnmatch(name, first.pattern) and self._match_labels(labels, first):
                eid = store._name_to_id(name)
                if eid:
                    current_ids.add(eid)

        # 也搜索虚实体（目录 + 未索引文件）
        for vkey, vname, vlabels, vtype in store.discover_virtual(first.pattern):
            if fnmatch(vname, first.pattern) and self._match_labels(vlabels, first):
                if vtype == "dir":
                    current_ids.add(f"__vdir__{vkey}")
                else:
                    current_ids.add(f"__vfile__{vkey}")

        # 后续段：沿边遍历
        for seg in segments[1:]:
            next_ids = set()
            target_store = store

            # 跨 store 段
            if seg.project:
                target_store = self.get_store(seg.project)
                if not target_store:
                    current_ids = set()
                    break

            for eid in current_ids:
                # 虚目录节点：通过 store 获取邻接
                if isinstance(eid, str) and eid.startswith("__vdir__"):
                    vkey = eid[len("__vdir__"):]
                    for child_key, child_name, child_labels in store.get_virtual_neighbors(vkey):
                        if fnmatch(child_name, seg.pattern) and self._match_labels(child_labels, seg):
                            if isinstance(child_key, str) and child_key.startswith("ent_"):
                                next_ids.add(child_key)  # 已持久化的文件实体
                            else:
                                next_ids.add(f"__vdir__{child_key}")  # 子目录虚节点
                    continue

                # 跳过虚文件节点（叶子）
                if isinstance(eid, str) and eid.startswith("__vfile__"):
                    continue

                # 持久化实体：沿 store 边遍历
                for adj_eid in store._adjacent.get(eid, set()):
                    adj_name = store._id_index.get(adj_eid)
                    if not adj_name:
                        continue
                    adj_labels = store._get_labels_by_id(adj_eid)

                    if fnmatch(adj_name, seg.pattern) and self._match_labels(adj_labels, seg):
                        if target_store is store:
                            next_ids.add(adj_eid)
                        else:
                            t_eid = target_store._name_to_id(adj_name)
                            if t_eid:
                                next_ids.add(t_eid)

            # 如果跨 store，也在目标 store 的所有实体中搜
            if seg.project and target_store:
                for name, labels in target_store.list_all():
                    if fnmatch(name, seg.pattern) and self._match_labels(labels, seg):
                        t_eid = target_store._name_to_id(name)
                        if t_eid:
                            next_ids.add(t_eid)

            current_ids = next_ids

        # 转换为 (name, labels) 结果
        results = []
        for eid in current_ids:
            if isinstance(eid, str) and eid.startswith("__vdir__"):
                # 虚目录节点：取裸名
                probe_key = eid[len("__vdir__"):]
                bare = os.path.basename(probe_key) if probe_key != "." else "."
                results.append((bare, ["dir"]))
            elif isinstance(eid, str) and eid.startswith("__vfile__"):
                vkey = eid[len("__vfile__"):]
                name = os.path.basename(vkey)
                results.append((name, []))
            else:
                name = store._id_index.get(eid)
                if name:
                    labels = store._get_labels_by_id(eid)
                    results.append((name, labels))
        return results

    def _match_segment_in_store(self, store: "Store",
                                 seg: Segment) -> List[Tuple[str, List[str]]]:
        """在单个 store 内匹配单段。"""
        results = []
        for name, labels in store.list_all():
            if fnmatch(name, seg.pattern) and self._match_labels(labels, seg):
                results.append((name, labels))

        # 也搜索虚实体（目录 + 未索引文件）
        for vkey, vname, vlabels, vtype in store.discover_virtual(seg.pattern):
            if fnmatch(vname, seg.pattern) and self._match_labels(vlabels, seg):
                results.append((vname, vlabels))

        return results

    # ── 标签匹配 ──

    @staticmethod
    def label_matches(entity_labels: List[str], query_label: str) -> bool:
        """扁平标签匹配。query_label 是否在 entity_labels 中。"""
        from storage.labels import label_matches
        return label_matches(entity_labels, query_label)

    def _match_labels(self, entity_labels: List[str], seg: Segment) -> bool:
        """AND: 所有 labels_and 必须匹配。OR: 至少一个 labels_or 匹配。"""
        if seg.labels_and:
            if not all(self.label_matches(entity_labels, t) for t in seg.labels_and):
                return False
        if seg.labels_or:
            if not any(self.label_matches(entity_labels, t) for t in seg.labels_or):
                return False
        return True
