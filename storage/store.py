"""Store — 唯一主图实现。"""

from __future__ import annotations

import threading
import logging
import time
from dataclasses import dataclass
from typing import Dict

from storage.config import SourceConfig
from storage.neo4j import Neo4jGraph

_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: Dict[str, threading.RLock] = {}
_PUBLISH_STATE_GUARD = threading.Lock()
_PUBLISH_STATE: Dict[tuple, "_PublishState"] = {}
logger = logging.getLogger(__name__)


@dataclass
class _PublishState:
    published_at: float
    fingerprint: str | None = None


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

    def clear_graph(self) -> None:
        """Remove all nodes and relationships for this project graph."""
        with self.execution_lock:
            self.execute_cypher("MATCH (n) DETACH DELETE n")
            self.invalidate_modules()

    def publish_modules(self, modules: list, *, force: bool = False) -> None:
        """Execute selected source-module Cypher submissions."""
        for mod in modules:
            if not force and self._module_is_fresh(mod):
                continue
            try:
                statements = list(mod.cypher_statements())
            except Exception as exc:
                logger.exception("Source module %s failed to build Cypher statements", mod.name)
                raise RuntimeError(f"Source module {mod.name} failed") from exc
            for statement in statements:
                self.execute_cypher(statement.query, params=statement.params)
            self._mark_module_published(mod)

    def invalidate_modules(self, module_names: list[str] | None = None) -> None:
        """Invalidate query-time publish cache for this project."""
        names = set(module_names or [])
        prefix = self._module_state_prefix()
        with _PUBLISH_STATE_GUARD:
            for key in list(_PUBLISH_STATE.keys()):
                if key[:3] != prefix:
                    continue
                if names and key[3] not in names:
                    continue
                _PUBLISH_STATE.pop(key, None)

    def _module_state_prefix(self) -> tuple:
        graph_uri = getattr(self._graph, "uri", "") or ""
        graph_db = getattr(self._graph, "database", "") or ""
        return (self._project_path, graph_uri, graph_db)

    def _module_state_key(self, mod) -> tuple:
        return (*self._module_state_prefix(), getattr(mod, "name", mod.__class__.__name__))

    def _module_is_fresh(self, mod) -> bool:
        key = self._module_state_key(mod)
        now = time.monotonic()
        with _PUBLISH_STATE_GUARD:
            state = _PUBLISH_STATE.get(key)
        if state is None:
            return False

        ttl = float(getattr(mod, "refresh_interval_seconds", 0.0) or 0.0)
        if ttl > 0 and now - state.published_at < ttl:
            return True

        try:
            fingerprint = mod.source_fingerprint()
        except Exception:
            logger.exception("Source module %s failed source fingerprint", mod.name)
            return False
        if fingerprint is not None and fingerprint == state.fingerprint:
            with _PUBLISH_STATE_GUARD:
                _PUBLISH_STATE[key] = _PublishState(published_at=now, fingerprint=fingerprint)
            return True
        return False

    def _mark_module_published(self, mod) -> None:
        try:
            fingerprint = mod.source_fingerprint()
        except Exception:
            logger.exception("Source module %s failed source fingerprint", mod.name)
            fingerprint = None
        with _PUBLISH_STATE_GUARD:
            _PUBLISH_STATE[self._module_state_key(mod)] = _PublishState(
                published_at=time.monotonic(),
                fingerprint=fingerprint,
            )
