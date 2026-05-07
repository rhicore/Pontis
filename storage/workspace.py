"""Workspace — 顶层容器，统一创建入口和路由。"""
from storage.config import StoreConfig, load_config
from storage.finder import Finder
from storage import stores

import logging

logger = logging.getLogger(__name__)


class Workspace:
    def __init__(self, config_path: str = None, project_path: str = None,
                 active_projects: list = None):
        self._config = load_config(config_path, project_path)
        self._finder = Finder(self._config)

        # 确定要注册的项目列表
        if active_projects:
            for pname in active_projects:
                self._register_project(pname)

    def _register_project(self, name: str):
        path = self._config.resolve_path(name)
        if not path:
            logger.warning("Project '%s' not found in config, skipping", name)
            return
        backend = self._config.resolve_backend(name)
        store = stores.create_store(backend, path)
        store._project_name = name
        self._finder.register_store(name, store)

    @property
    def finder(self) -> Finder:
        return self._finder

    @property
    def config(self) -> StoreConfig:
        return self._config

    @property
    def active_projects(self) -> list:
        return list(self._finder._stores.keys())

    def get_store(self, project: str = None):
        """获取指定 project 的 Store，默认返回唯一已注册 store 或首个注册 store。"""
        if project:
            return self._finder.get_store(project)
        stores = self._finder._stores
        if len(stores) == 1:
            return next(iter(stores.values()))
        if stores:
            return next(iter(stores.values()))
        return None

    def create_entity(self, ref: str, *, meta: dict = None,
                      edges: list = None, labels: list = None,
                      project: str = None) -> str:
        """统一创建入口。

        路由逻辑：
        1. 显式 project 参数 → 直接用
        2. config routing 规则匹配 → 自动路由
        3. 兜底 → default project
        """
        target = project or self._config.route_entity(ref) or self._config.default_project()
        store = self._finder.get_store(target)
        if not store:
            return f"Error: no store for project '{target}'"
        return store.create_node(ref, meta=meta, edges=edges, labels=labels)

    def query(self, cypher: str) -> list:
        """执行 Cypher 查询，返回 [{"var": {"name": ..., "labels": ...}}, ...]。"""
        from storage.cypher import parse_cypher, CypherExecutor
        store = self.get_store()
        if not store:
            return []
        executor = CypherExecutor(store)
        return executor.execute(parse_cypher(cypher))
