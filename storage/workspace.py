"""Workspace — 顶层容器，统一创建入口和路由。"""
from storage.config import StoreConfig, load_config
from storage.finder import Finder
from storage import stores

import logging

logger = logging.getLogger(__name__)


class Workspace:
    def __init__(self, config_path: str = None, project_path: str = None):
        self._config = load_config(config_path, project_path)
        self._finder = Finder(self._config)

        # 自动注册 default project 的 store
        dp = self._config.default_project()
        if dp:
            path = self._config.resolve_path(dp)
            if path:
                backend = self._config.resolve_backend(dp)
                store = stores.create_store(backend, path)
                self._finder.register_store(dp, store)

    @property
    def finder(self) -> Finder:
        return self._finder

    @property
    def config(self) -> StoreConfig:
        return self._config

    def get_store(self, project: str = None):
        """获取指定 project 的 Store，默认返回 default store。"""
        if project:
            return self._finder.get_store(project)
        dp = self._config.default_project()
        return self._finder.get_store(dp) if dp else None

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
