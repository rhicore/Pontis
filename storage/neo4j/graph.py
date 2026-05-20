"""Neo4j graph store adapter.

This is the only durable graph storage implementation. It owns Neo4j
connection management, node/edge upserts, and native Cypher execution result
normalization.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


_DRIVERS_GUARD = threading.Lock()
_DRIVERS = {}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export "):]
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _local_env() -> dict[str, str]:
    """Load ignored local Neo4j env files without requiring shell sourcing."""
    pontis_root = Path(__file__).resolve().parents[2]
    text2sql_root = pontis_root.parent
    candidates = [
        Path.cwd() / ".neo4j" / "neo4j.env",
        text2sql_root / "workspace" / "baselines" / "pontis" / "neo4j" / "neo4j.env",
        pontis_root / ".neo4j" / "neo4j.env",
    ]
    loaded: dict[str, str] = {}
    for path in candidates:
        loaded.update(_read_env_file(path))
    return loaded


def _node_to_dict(node) -> dict:
    data = dict(node)
    labels = list(getattr(node, "labels", []))
    if "labels" not in data:
        data["labels"] = labels
    if "id" not in data:
        data["id"] = getattr(node, "element_id", "")
    return data


def _rel_to_dict(rel) -> dict:
    data = dict(rel)
    data.setdefault("type", getattr(rel, "type", ""))
    data.setdefault("id", getattr(rel, "element_id", ""))
    return data


def _normalize_value(value):
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(v) for v in value)
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}

    try:
        from neo4j.graph import Node, Relationship, Path
    except ImportError:
        return value

    if isinstance(value, Node):
        return _node_to_dict(value)
    if isinstance(value, Relationship):
        return _rel_to_dict(value)
    if isinstance(value, Path):
        return {
            "nodes": [_node_to_dict(n) for n in value.nodes],
            "relationships": [_rel_to_dict(r) for r in value.relationships],
        }
    return value


class Neo4jGraph:
    """Neo4j-backed durable graph."""

    def __init__(
        self,
        uri: str = "",
        database: str = "",
        user: str = "",
        password: str = "",
        password_env: str = "",
        **_,
    ):
        local_env = _local_env()
        self.uri = uri or os.environ.get("NEO4J_URI") or local_env.get("NEO4J_URI") or "bolt://localhost:7687"
        self.database = database or os.environ.get("NEO4J_DATABASE") or local_env.get("NEO4J_DATABASE", "")
        self.user = user or os.environ.get("NEO4J_USER") or local_env.get("NEO4J_USER") or "neo4j"
        self.password = password or (
            os.environ.get(password_env) or local_env.get(password_env, "") if password_env else ""
        ) or os.environ.get("NEO4J_PASSWORD", "") or local_env.get("NEO4J_PASSWORD", "")
        self.password_env = password_env
        self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            self.connect()
        return self._driver

    def connect(self):
        if self._driver is not None:
            return
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError(
                "Neo4j storage requires the 'neo4j' Python package. "
                "Install it with: pip install neo4j"
            ) from exc
        if self.user and not self.password:
            hint = (
                f" env var {self.password_env!r}" if self.password_env
                else " NEO4J_PASSWORD"
            )
            raise RuntimeError(
                "Missing Neo4j password."
                f" Set{hint}, or create .neo4j/neo4j.env with NEO4J_PASSWORD=..."
            )
        auth = (self.user, self.password) if self.user else None
        key = (self.uri, self.user, self.password)
        with _DRIVERS_GUARD:
            driver = _DRIVERS.get(key)
            if driver is None:
                driver = GraphDatabase.driver(
                    self.uri,
                    auth=auth,
                    notifications_min_severity="OFF",
                    warn_notification_severity="OFF",
                )
                _DRIVERS[key] = driver
            self._driver = driver

    def close(self):
        self._driver = None

    def _session(self):
        kwargs = {"database": self.database} if self.database else {}
        return self.driver.session(**kwargs)

    def execute_cypher(self, query: str, params: dict | None = None) -> list[dict]:
        try:
            from neo4j.exceptions import ServiceUnavailable, SessionExpired
        except ImportError:
            ServiceUnavailable = SessionExpired = ()  # type: ignore[assignment]

        last_exc = None
        for attempt in range(3):
            try:
                return self._execute_cypher_once(query, params=params)
            except Exception as exc:
                if not isinstance(exc, (ServiceUnavailable, SessionExpired)):
                    raise
                last_exc = exc
                self.close()
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def _execute_cypher_once(self, query: str, params: dict | None = None) -> list[dict]:
        with self._session() as session:
            result = session.run(query, params or {})
            return [
                {key: _normalize_value(record[key]) for key in record.keys()}
                for record in result
            ]
