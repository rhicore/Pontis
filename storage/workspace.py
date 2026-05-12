"""Workspace — 顶层容器，统一创建入口和路由。"""
import os
from dataclasses import replace
from storage.config import StoreConfig, load_config
from storage import stores
from storage.stores.base import MatchResult
from storage.merged import MergedStoreView

import logging

logger = logging.getLogger(__name__)


class Workspace:
    def __init__(self, config_path: str = None, project_path: str = None,
                 active_projects: list = None):
        self._config = load_config(config_path, project_path)
        self._stores: dict = {}  # project_name → Store
        self._modules: dict = {}  # project_name → [StoreModule, ...]

        # 确定要激活的项目列表
        if active_projects:
            names = active_projects
        elif project_path:
            # project_path 模式：只激活从 project_path 自动注册的那个项目
            pname = os.path.basename(os.path.abspath(project_path))
            names = [pname] if pname in self._config.projects else []
        else:
            names = []

        for pname in names:
            self._register_project(pname)

    def _register_project(self, name: str):
        config = self._config.projects.get(name)
        if not config:
            logger.warning("Project '%s' not found in config, skipping", name)
            return
        store = stores.create_store(config)
        store._project_name = name
        self._stores[name] = store
        self._modules[name] = list(getattr(store, "modules", []))

    @property
    def config(self) -> StoreConfig:
        return self._config

    @property
    def active_projects(self) -> list:
        return list(self._stores.keys())

    def modules(self, project: str = None) -> list:
        """返回指定项目的模块列表。"""
        if not hasattr(self, "_modules"):
            self._modules = {}
        if project:
            mods = self._modules.get(project)
            if mods is not None:
                return list(mods)
            store = self._stores.get(project)
            return list(getattr(store, "modules", [])) if store else []
        store = self._get_store(project)
        if not store:
            return []
        pname = getattr(store, "_project_name", "")
        mods = self._modules.get(pname)
        if mods is not None:
            return list(mods)
        return list(getattr(store, "modules", []))

    def _get_store(self, project: str = None):
        """获取指定 project 的 Store，默认返回唯一已注册 store 或首个注册 store。"""
        if project:
            return self._stores.get(project)
        if len(self._stores) == 1:
            return next(iter(self._stores.values()))
        if self._stores:
            return next(iter(self._stores.values()))
        return None

    @property
    def project_path(self) -> str:
        store = self._get_store()
        return store.project_path if store else ""

    @property
    def index_root(self) -> str:
        store = self._get_store()
        return store.index_root if store else ""

    @property
    def pontis_exists(self) -> bool:
        store = self._get_store()
        return store.pontis_exists if store else False

    # ── Graph API (Cypher only) ──

    def cypher(self, query: str, params: dict = None, project: str = None) -> list:
        """执行 Cypher 查询，实体访问面只暴露 id/project/labels/src。

        Args:
            query: Cypher 查询字符串
            params: 参数字典（$var 替换）
            project: 指定项目，None 时使用默认项目
        """
        from storage.cypher import parse_cypher, CypherExecutor
        store = self._get_store(project)
        if not store:
            return []
        with store.execution_lock:
            parsed = parse_cypher(query, params=params)
            target_store = store
            if parsed.action == "RETURN":
                project_name = getattr(store, "_project_name", "")
                modules = self.modules(project_name)
                if modules:
                    target_store = MergedStoreView(store, modules)
            else:
                self._materialize_for_write(parsed, store, project=project)
            executor = CypherExecutor(target_store)
            return executor.execute(parsed)

    def _materialize_for_write(self, parsed, store, project: str = None):
        """Materialize virtual MATCH results as part of Cypher write execution."""
        if parsed.action == "CREATE" and not parsed.create_rels:
            return
        project_name = getattr(store, "_project_name", "")
        modules = self.modules(project_name)
        if not modules:
            return

        match_query = replace(parsed, action="RETURN", return_items=[], return_vars=[
            n.var for n in parsed.nodes if n.var
        ])
        from storage.cypher import CypherExecutor
        view = MergedStoreView(store, modules)
        rows = CypherExecutor(view).execute(match_query)
        for row in rows:
            for node in row.values():
                if not isinstance(node, dict):
                    continue
                eid = node.get("id")
                if not eid or eid in store._id_index:
                    continue
                ref = node.get("path") or node.get("ref") or node.get("name")
                if ref:
                    seed_meta = {
                        k: v for k, v in node.items()
                        if k not in ("id", "project", "labels", "src")
                    }
                    if "labels" in node:
                        seed_meta["labels"] = list(node.get("labels", []))
                    self._materialize(ref, project=project, seed_meta=seed_meta)

    def _collect_virtual_meta(self, ref: str, project: str = None) -> dict | None:
        """从模块收集虚元数据。

        规则：
        - 模块返回的虚属性覆盖已有结果
        - `labels` 做并集
        """
        store = self._get_store(project)
        if not store:
            return None
        project_name = getattr(store, "_project_name", "")
        merged = None
        for mod in self.modules(project_name):
            try:
                meta = mod.get_virtual_meta(ref)
            except Exception:
                meta = None
            if not meta:
                continue
            meta = dict(meta)
            if merged is None:
                merged = meta
                continue
            labels = set(merged.get("labels", [])) | set(meta.get("labels", []))
            merged.update(meta)
            if labels:
                merged["labels"] = sorted(labels)
        return merged

    def _collect_virtual_neighbors(self, ref: str, project: str = None) -> list:
        store = self._get_store(project)
        if not store:
            return []
        project_name = getattr(store, "_project_name", "")
        results = []
        for mod in self.modules(project_name):
            try:
                results.extend(mod.get_virtual_neighbors(ref))
            except Exception:
                continue
        return results

    def _collect_materialize_query_matches(self, vnode: dict, project: str = None) -> MatchResult:
        store = self._get_store(project)
        if not store:
            return MatchResult(matches=[], mergeable=False)

        project_name = getattr(store, "_project_name", "")
        modules = self.modules(project_name)
        matches = []
        mergeable = True
        for mod in modules:
            try:
                q = mod.match_query(vnode)
            except Exception:
                q = None
            if q is None:
                continue
            rows = store._cypher_internal(q.query, params=q.params)
            row_matches = []
            for row in rows:
                item = row.get(q.var)
                if isinstance(item, dict):
                    ent_id = item.get("id", "")
                    if ent_id and ent_id not in row_matches:
                        row_matches.append(ent_id)
            if len(row_matches) > 1:
                mergeable = False
            for m in row_matches:
                if m not in matches:
                    matches.append(m)

        if len(matches) > 1:
            mergeable = False
        return MatchResult(matches=matches, mergeable=mergeable)

    def _materialize_target_for_virtual(self, ref: str, vmeta: dict, project: str = None) -> str:
        store = self._get_store(project)
        if not store:
            return ""

        vnode = dict(vmeta)
        vnode.setdefault("labels", list(vmeta.get("labels", [])))
        m = self._collect_materialize_query_matches(vnode, project=project)
        if m.mergeable and len(m.matches) == 1 and m.matches[0] in store._id_index:
            return m.matches[0]
        return ""

    def _materialize_closure(self, ref: str, project: str = None) -> tuple[list[str], list[tuple[str, str]]]:
        store = self._get_store(project)
        if not store:
            return [], []

        queue = [ref]
        seen = set()
        closure = []
        edges = []

        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            closure.append(current)
            for item in self._collect_virtual_neighbors(current, project=project):
                if isinstance(item, (tuple, list)) and item:
                    child_ref = item[0]
                else:
                    child_ref = item
                if not isinstance(child_ref, str) or not child_ref:
                    continue
                edges.append((current, child_ref))
                if child_ref not in seen:
                    queue.append(child_ref)
        return closure, edges

    def _materialize(self, ref: str, project: str = None, seed_meta: dict | None = None) -> str:
        """Cypher 写路径内部物化入口。

        - 已持久化实体：直接返回
        - 虚实体：按模块元数据物化
        - 若已有持久化实体，再次物化时虚属性覆盖旧值
        """
        store = self._get_store(project)
        if not store:
            return ""

        closure, virtual_edges = self._materialize_closure(ref, project=project)
        if not closure:
            closure = [ref]

        resolved: dict[str, str] = {}

        for current in closure:
            vmeta = self._collect_virtual_meta(current, project=project)
            if seed_meta and current == ref:
                merged_seed = dict(vmeta or {})
                labels = set(merged_seed.get("labels", [])) | set(seed_meta.get("labels", []))
                merged_seed.update(seed_meta)
                if labels:
                    merged_seed["labels"] = sorted(labels)
                vmeta = merged_seed
            ent_id = ""

            if vmeta:
                ent_id = self._materialize_target_for_virtual(current, vmeta, project=project)
            if not ent_id:
                ent_id = store._resolve_to_id(current)

            if ent_id and not vmeta:
                resolved[current] = ent_id
                continue

            if not vmeta:
                resolved[current] = store._create_node(current)
                continue

            labels = list(vmeta.get("labels", []))
            payload = {k: v for k, v in vmeta.items() if k != "labels"}

            if ent_id:
                existing = store._read_raw(ent_id) or {}
                merged = dict(existing)
                merged.update(payload)
                merged["labels"] = sorted(set(existing.get("labels", [])) | set(labels))
                store._write_entity_meta(ent_id, merged)
                store._meta_cache[ent_id] = dict(merged)
                store._register_node(ent_id, merged)
                store._mark_dirty()
                resolved[current] = ent_id
            else:
                resolved[current] = store._create_node(current, meta=payload, labels=labels or None)

        edge_payloads = []
        for a_ref, b_ref in virtual_edges:
            a_id = resolved.get(a_ref) or store._resolve_to_id(a_ref)
            b_id = resolved.get(b_ref) or store._resolve_to_id(b_ref)
            if a_id and b_id and a_id != b_id:
                edge_payloads.append({"a": a_id, "b": b_id})
        if edge_payloads:
            store._add_edges(edge_payloads)

        return resolved.get(ref, store._resolve_to_id(ref) or "")

    def module_subgraph(self, project: str = None) -> dict:
        """导出指定项目所有模块提供的子图。

        返回：
        {
          "nodes": [...],
          "edges": [("a", "b"), ...],
        }
        """
        store = self._get_store(project)
        if not store:
            return {"nodes": [], "edges": []}

        project_name = getattr(store, "_project_name", "")
        modules = self.modules(project_name)
        all_nodes = []
        all_edges = []

        for mod in modules:
            try:
                nodes = list(mod.iter_virtual_nodes())
            except Exception:
                nodes = []
            try:
                edges = list(mod.iter_virtual_edges(nodes))
            except Exception:
                edges = []
            all_nodes.extend(nodes)
            all_edges.extend(edges)

        return {"nodes": all_nodes, "edges": all_edges}

    # ==================== Cross Project ====================

    def _add_cross_ref(self, from_project: str, from_entity: str,
                       to_project: str, to_entity_id: str):
        """建立跨项目关联。（仅供内部/debug 使用）

        Args:
            from_project: 源项目名
            from_entity: 源实体名（在 from_project Store 中）
            to_project: 目标项目名
            to_entity_id: 目标实体的 ent_id（必须是已物化的 ent_xxx）

        Raises:
            KeyError: 源实体不存在
            ValueError: to_entity_id 不是 ent_xxx 格式
        """
        if not to_entity_id.startswith("ent_"):
            raise ValueError(
                f"Cross-ref target must be a materialized entity ID (ent_xxx), "
                f"got '{to_entity_id}'")

        from_store = self._stores[from_project]
        from_id = from_store._resolve_to_id(from_entity)
        if not from_id:
            raise KeyError(f"Entity '{from_entity}' not found in '{from_project}'")

        from_store._add_cross_edge(from_id, to_project, to_entity_id)

    def _resolve_cross_refs(self, project: str = None) -> list:
        """解析指定项目的所有跨项目引用。（仅供内部/debug 使用）

        Returns:
            [
                {"from": "ent_xxx", "to_project": "bird", "to_entity_id": "ent_yyy",
                 "status": "ok", "to_entity": {...}},
                {"from": "ent_xxx", "to_project": "bird", "to_entity_id": "ent_yyy",
                 "status": "target_missing"},
                ...
            ]
        """
        store = self._get_store(project)
        if not store:
            return []
        results = []
        for from_id, refs in store._cross_adjacent.items():
            for ref in refs:
                entry = {
                    "from": from_id,
                    "to_project": ref["to_project"],
                    "to_entity_id": ref["to_entity_id"],
                }

                if ref.get("stale"):
                    entry["status"] = "target_missing"
                    results.append(entry)
                    continue

                to_store = self._stores.get(ref["to_project"])
                if not to_store:
                    entry["status"] = "project_unavailable"
                    results.append(entry)
                    continue

                try:
                    to_props = to_store._id_index.get(ref["to_entity_id"])
                    if to_props:
                        entry["status"] = "ok"
                        entry["to_entity"] = {
                            "id": ref["to_entity_id"],
                            "labels": to_props.get("labels", []),
                        }
                    else:
                        store._mark_cross_edge_stale(
                            from_id, ref["to_project"], ref["to_entity_id"])
                        entry["status"] = "target_missing"
                except Exception as e:
                    entry["status"] = "error"
                    entry["detail"] = str(e)

                results.append(entry)
        return results

    def _purge_stale_refs(self, project: str = None) -> int:
        """清理所有 stale 的跨项目引用。返回清理数量。（仅供内部/debug 使用）"""
        store = self._get_store(project)
        if not store:
            return 0
        stale_keys = []
        for from_id, refs in store._cross_adjacent.items():
            for ref in refs:
                if ref.get("stale"):
                    stale_keys.append((from_id, ref["to_project"], ref["to_entity_id"]))

        for from_id, to_project, to_entity_id in stale_keys:
            store._remove_cross_edge(from_id, to_project, to_entity_id)
        return len(stale_keys)
