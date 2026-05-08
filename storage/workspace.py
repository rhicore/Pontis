"""Workspace — 顶层容器，统一创建入口和路由。"""
import os
from storage.config import StoreConfig, load_config
from storage import stores

import logging

logger = logging.getLogger(__name__)


class Workspace:
    def __init__(self, config_path: str = None, project_path: str = None,
                 active_projects: list = None):
        self._config = load_config(config_path, project_path)
        self._stores: dict = {}  # project_name → Store

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

    @property
    def config(self) -> StoreConfig:
        return self._config

    @property
    def active_projects(self) -> list:
        return list(self._stores.keys())

    def _get_store(self, project: str = None):
        """获取指定 project 的 Store，默认返回唯一已注册 store 或首个注册 store。"""
        if project:
            return self._stores.get(project)
        if len(self._stores) == 1:
            return next(iter(self._stores.values()))
        if self._stores:
            return next(iter(self._stores.values()))
        return None

    # ── Data access proxies ──

    @property
    def project_path(self) -> str:
        store = self._get_store()
        return store.project_path if store else ""

    def data_exists(self, rel_path: str, project: str = None) -> bool:
        store = self._get_store(project)
        return store.data_exists(rel_path) if store else False

    def resolve_data_path(self, rel_path: str, project: str = None) -> str:
        store = self._get_store(project)
        return store.resolve_data_path(rel_path) if store else ""

    def open_db(self, rel_path: str, project: str = None):
        store = self._get_store(project)
        return store.open_db(rel_path) if store else None

    def open_file(self, rel_path: str, mode='r', project: str = None, **kwargs):
        store = self._get_store(project)
        return store.open_file(rel_path, mode=mode, **kwargs) if store else None

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
        """执行 Cypher 查询，返回 [{"var": {"name": ..., "labels": ...}}, ...]。

        Args:
            query: Cypher 查询字符串
            params: 参数字典（$var 替换）
            project: 指定项目，None 时使用默认项目
        """
        from storage.cypher import parse_cypher, CypherExecutor
        store = self._get_store(project)
        if not store:
            return []
        executor = CypherExecutor(store)
        return executor.execute(parse_cypher(query, params=params))

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
                {"from": "name", "to_project": "bird", "to_entity_id": "ent_xxx",
                 "status": "ok", "to_entity": {...}},
                {"from": "name", "to_project": "bird", "to_entity_id": "ent_xxx",
                 "status": "target_missing"},
                ...
            ]
        """
        store = self._get_store(project)
        if not store:
            return []
        results = []
        for from_id, refs in store._cross_adjacent.items():
            from_name = store._id_index.get(from_id, {}).get("name", "")
            for ref in refs:
                entry = {
                    "from": from_name,
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
                            "name": to_props.get("name", ""),
                            "labels": to_props.get("_labels", []),
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
