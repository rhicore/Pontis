"""Merged graph view for Workspace read queries."""

from __future__ import annotations

from typing import Dict, List, Tuple


class MergedStoreView:
    """只读合并视图。

    - 主图：持久化实体/边
    - 模块：虚实体/虚边
    - 匹配到唯一主图实体时，保守合并到主图
    - 写操作不经过这个视图
    """

    def __init__(self, base_store, modules: list):
        self.base = base_store
        self.modules = modules
        self._project_name = getattr(base_store, "_project_name", "")
        self.internal_fields = getattr(base_store, "internal_fields", set())
        self._id_index: Dict[str, dict] = {}
        self._adjacent: Dict[str, set] = {}
        self._merged_paths: Dict[str, str] = {}
        self._virtual_src_nodes: Dict[str, dict] = {}
        self._build()

    def _build(self):
        self.base._ensure_index()

        # 1) 持久化主图
        persisted = list(self.base._scan_entities())
        for ent_id, raw in persisted:
            self._id_index[ent_id] = dict(raw)
            self._adjacent.setdefault(ent_id, set())

        for e in self.base._read_edges_storage():
            nodes = e.get("nodes", [])
            if len(nodes) != 2:
                continue
            a, b = nodes
            if a in self._id_index and b in self._id_index:
                self._adjacent.setdefault(a, set()).add(b)
                self._adjacent.setdefault(b, set()).add(a)

        # 2) 模块虚图
        vid_counter = 0
        for mod in self.modules:
            nodes = list(mod.iter_virtual_nodes())
            key_to_id = {}

            for node in nodes:
                vnode = dict(node)
                vnode["labels"] = list(vnode.get("labels", []))

                q = mod.match_query(vnode)
                m = None
                if q is not None:
                    rows = self.base._cypher_internal(q.query, params=q.params)
                    qmatches = []
                    for row in rows:
                        item = row.get(q.var)
                        if isinstance(item, dict):
                            ent_id = item.get("id", "")
                            if ent_id and ent_id not in qmatches:
                                qmatches.append(ent_id)
                    from storage.stores.base import MatchResult
                    m = MatchResult(matches=qmatches, mergeable=len(qmatches) <= 1)
                if m is not None and m.mergeable and len(m.matches) == 1 and m.matches[0] in self._id_index:
                    eid = m.matches[0]
                    base_raw = self._id_index[eid]
                    merged = dict(base_raw)
                    for k, v in vnode.items():
                        if k == "labels":
                            continue
                        merged[k] = v
                    merged["labels"] = sorted(
                        set(base_raw.get("labels", [])) | set(vnode.get("labels", []))
                    )
                    self._id_index[eid] = merged
                    for key in self._node_keys(vnode):
                        self._merged_paths[key] = eid
                        key_to_id[key] = eid
                else:
                    vid_counter += 1
                    vid = f"_mv_{mod.name}_{vid_counter}"
                    self._id_index[vid] = vnode
                    self._adjacent.setdefault(vid, set())
                    for key in self._node_keys(vnode):
                        self._merged_paths[key] = vid
                        key_to_id[key] = vid
                    self._virtual_src_nodes[vid] = vnode

            for a_path, b_path in mod.iter_virtual_edges(nodes):
                a_id = key_to_id.get(a_path) or self._merged_paths.get(a_path)
                b_id = key_to_id.get(b_path) or self._merged_paths.get(b_path)
                if not a_id or not b_id or a_id == b_id:
                    continue
                self._adjacent.setdefault(a_id, set()).add(b_id)
                self._adjacent.setdefault(b_id, set()).add(a_id)

    def _node_keys(self, vnode: dict) -> list[str]:
        keys = []
        for key in (vnode.get("path"), vnode.get("_path"), vnode.get("ref")):
            if key and key not in keys:
                keys.append(key)
        return keys

    def _ensure_index(self):
        return

    def _get_meta(self, ref: str, include_props=None, _visiting=None):
        if ref in self._id_index:
            return dict(self._id_index[ref])
        return None

    def bind_src(self, node: dict):
        eid = node.get("id") if isinstance(node, dict) else None
        if eid and eid in self._id_index:
            node = dict(self._id_index[eid])
            node["id"] = eid
        # 先让模块试绑定，再退回主图
        for mod in self.modules:
            try:
                src = mod.bind_src(node)
            except Exception:
                src = None
            if src is not None:
                return src
        return self.base.bind_src(node)
