"""Store — 唯一主图实现。"""

from __future__ import annotations

import threading
import logging
from typing import Dict

from storage.config import SourceConfig
from storage.neo4j import Neo4jGraph

_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: Dict[str, threading.RLock] = {}
logger = logging.getLogger(__name__)


def _lock_key_for_store(project_path: str) -> str:
    if project_path:
        import os
        return f"project:{os.path.abspath(project_path)}"
    return "project:<anonymous>"


def _get_store_lock(project_path: str) -> threading.RLock:
    key = _lock_key_for_store(project_path)
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock


class Store:
    """Project graph coordinator.

    - Neo4jGraph 负责物理持久化和 Cypher 执行
    - Store 负责按模块声明的 Cypher 事务刷新源事实
    """

    def __init__(self, source_config: SourceConfig, graph: Neo4jGraph):
        import os
        self._source_config = source_config
        self._graph = graph
        self._graph.connect()

        self._project_path = os.path.abspath(source_config.path) if source_config.path else ""
        self._project_name = ""
        self._execution_lock = _get_store_lock(self._project_path)

        self._modules: list = []

    # ==================== properties ====================

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def project_name(self) -> str:
        return self._project_name

    def set_project_name(self, name: str):
        self._project_name = name or ""

    @property
    def modules(self) -> list:
        return list(self._modules)

    def add_module(self, module):
        self._modules.append(module)

    @property
    def execution_lock(self):
        return self._execution_lock

    # ==================== native query / module publishing ====================

    def execute_cypher(self, query: str, params: dict = None) -> list:
        return self._graph.execute_cypher(query, params=params)

    def publish_modules(self, modules: list) -> None:
        """Execute selected source-module Cypher submissions."""
        for mod in modules:
            try:
                statements = list(mod.cypher_statements())
            except Exception as exc:
                logger.exception("Source module %s failed to build Cypher statements", mod.name)
                raise RuntimeError(f"Source module {mod.name} failed") from exc
            for statement in statements:
                self.execute_cypher(statement.query, params=statement.params)
