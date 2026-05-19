"""Workspace — 顶层容器，统一创建入口和路由。"""
import os
from storage.config import StoreConfig, load_config
from storage import stores

import logging

from storage.stores.base import parse_pointer
from storage.triggers import TriggerEvent, TriggerRouter

logger = logging.getLogger(__name__)


class Workspace:
    def __init__(self, config_path: str = None, project_path: str = None,
                 active_projects: list = None):
        self._config = load_config(config_path, project_path)
        self._stores: dict = {}  # project_name → Store
        self._modules: dict = {}  # project_name → [StoreModule, ...]
        self._trigger_router = TriggerRouter()

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
        store.set_project_name(name)
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
        pname = store.project_name
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

    # ── Graph API (Cypher only) ──

    def cypher(self, query: str, params: dict = None, project: str = None) -> list:
        """执行 Cypher 查询，并在返回后解析 `<pontis:...>` 句柄值。

        Args:
            query: Cypher 查询字符串
            params: 参数字典（$var 替换）
            project: 指定项目，None 时使用默认项目
        """
        from storage.query_inspector import parse_cypher
        store = self._get_store(project)
        if not store:
            return []
        parsed = parse_cypher(query, params=params)
        event = TriggerEvent(
            type="write" if parsed.action != "RETURN" else "query",
            project=store.project_name,
            query=query,
            parsed_query=parsed,
            reason="cypher_write" if parsed.action != "RETURN" else "cypher_read",
        )
        modules = self._modules_for_event(self.modules(store.project_name), event)

        if event.type == "write":
            with store.execution_lock:
                if modules:
                    store.publish_modules(modules, force=True)
                rows = store.execute_cypher(query, params=params)
                store.invalidate_modules()
            return self._resolve_result_pointers(rows, store.project_name)

        if modules:
            with store.execution_lock:
                store.publish_modules(modules)

        rows = store.execute_cypher(query, params=params)
        return self._resolve_result_pointers(rows, store.project_name)

    def refresh_sources(self, project: str = None, modules: list[str] | None = None) -> None:
        """Force selected source modules to publish into Neo4j."""
        store = self._get_store(project)
        if not store:
            return
        selected = [
            mod for mod in self.modules(store.project_name)
            if not modules or mod.name in set(modules)
        ]
        with store.execution_lock:
            store.invalidate_modules(modules)
            store.publish_modules(selected, force=True)

    def _modules_for_query(self, modules: list, parsed, raw_query: str = "") -> list:
        event = TriggerEvent(type="query", project="", query=raw_query, parsed_query=parsed)
        return self._modules_for_event(modules, event)

    def _modules_for_event(self, modules: list, event: TriggerEvent) -> list:
        if not hasattr(self, "_trigger_router"):
            self._trigger_router = TriggerRouter()
        return self._trigger_router.select(modules, event)

    def _resolve_result_pointers(self, rows: list, project: str) -> list:
        return [
            {
                key: self._resolve_value_pointers(value, project)
                for key, value in row.items()
            }
            for row in rows
        ]

    def _resolve_value_pointers(self, value, project: str, node: dict | None = None):
        if isinstance(value, str):
            pointer = parse_pointer(value)
            if not pointer:
                return value
            target_project = pointer.project or project
            module_map = {mod.name: mod for mod in self.modules(target_project)}
            module = module_map.get(pointer.module)
            if not module:
                return value
            try:
                resolved = module.resolve_pointer(
                    pointer.kind,
                    pointer.payload,
                    node=node,
                )
            except Exception:
                logger.exception(
                    "Failed to resolve pointer from module=%s kind=%s",
                    pointer.module,
                    pointer.kind,
                )
                return value
            return value if resolved is None else resolved

        if isinstance(value, list):
            return [
                self._resolve_value_pointers(item, project, node=node)
                for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                self._resolve_value_pointers(item, project, node=node)
                for item in value
            )
        if isinstance(value, dict):
            original_node = value if ("id" in value or "labels" in value) else node
            return {
                key: self._resolve_value_pointers(item, project, node=original_node)
                for key, item in value.items()
            }
        return value
