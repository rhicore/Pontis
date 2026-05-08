"""SQLiteBackend — 基于 SQLite 的图数据库持久化后端。

使用 WAL 模式支持多进程并发读。
"""
import json
import sqlite3
import logging
from typing import Dict, List, Optional, Tuple

from storage.backends import GraphBackend

logger = logging.getLogger(__name__)


class SQLiteBackend(GraphBackend):
    """节点和边的 SQLite 存储。

    nodes 表: id TEXT PK, props TEXT (JSON)
    edges 表: a_id TEXT, b_id TEXT, PK(a_id, b_id)
    _meta 表: key TEXT PK, value TEXT — 全局元数据（版本号）
    cross_edges 表: 跨项目引用指针
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                props TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                a_id TEXT NOT NULL,
                b_id TEXT NOT NULL,
                PRIMARY KEY (a_id, b_id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS _meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            INSERT OR IGNORE INTO _meta (key, value) VALUES ('version', '0')
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cross_edges (
                from_id      TEXT NOT NULL,
                to_project   TEXT NOT NULL,
                to_entity_id TEXT NOT NULL,
                stale        INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (from_id, to_project, to_entity_id)
            )
        """)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ==================== Version ====================

    def read_version(self) -> int:
        cur = self._conn.execute("SELECT value FROM _meta WHERE key = 'version'")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def bump_version(self) -> int:
        self._conn.execute(
            "UPDATE _meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
            "WHERE key = 'version'")
        self._conn.commit()
        return self.read_version()

    # ==================== Nodes ====================

    def scan_nodes(self) -> List[Tuple[str, dict]]:
        cur = self._conn.execute("SELECT id, props FROM nodes")
        results = []
        for row in cur:
            try:
                results.append((row[0], json.loads(row[1])))
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Corrupt node: {row[0]}")
        return results

    def read_node(self, ent_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT props FROM nodes WHERE id = ?", (ent_id,))
        row = cur.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None

    def write_node(self, ent_id: str, props: dict):
        data = json.dumps(props, ensure_ascii=False, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO nodes (id, props) VALUES (?, ?)",
            (ent_id, data))
        self._conn.commit()

    def delete_node(self, ent_id: str):
        self._conn.execute("DELETE FROM nodes WHERE id = ?", (ent_id,))
        self._conn.commit()

    # ==================== Edges ====================

    def read_edges(self) -> List[dict]:
        cur = self._conn.execute("SELECT a_id, b_id FROM edges")
        return [{"nodes": [row[0], row[1]]} for row in cur]

    def write_edges(self, edges: List[dict]):
        self._conn.execute("DELETE FROM edges")
        self._conn.executemany(
            "INSERT OR IGNORE INTO edges (a_id, b_id) VALUES (?, ?)",
            [(e["nodes"][0], e["nodes"][1]) for e in edges if len(e.get("nodes", [])) == 2])
        self._conn.commit()

    def add_edge(self, a_id: str, b_id: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO edges (a_id, b_id) VALUES (?, ?)",
            (a_id, b_id))
        self._conn.execute(
            "INSERT OR IGNORE INTO edges (a_id, b_id) VALUES (?, ?)",
            (b_id, a_id))
        self._conn.commit()

    def remove_edges_for(self, ent_id: str):
        self._conn.execute(
            "DELETE FROM edges WHERE a_id = ? OR b_id = ?",
            (ent_id, ent_id))
        self._conn.commit()

    # ==================== Cross Edges ====================

    def add_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO cross_edges "
            "(from_id, to_project, to_entity_id, stale) VALUES (?, ?, ?, 0)",
            (from_id, to_project, to_entity_id))
        self._conn.commit()

    def remove_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        self._conn.execute(
            "DELETE FROM cross_edges "
            "WHERE from_id = ? AND to_project = ? AND to_entity_id = ?",
            (from_id, to_project, to_entity_id))
        self._conn.commit()

    def remove_cross_edges_for(self, from_id: str):
        self._conn.execute(
            "DELETE FROM cross_edges WHERE from_id = ?", (from_id,))
        self._conn.commit()

    def set_cross_edge_stale(self, from_id: str, to_project: str,
                             to_entity_id: str, *, stale: bool):
        self._conn.execute(
            "UPDATE cross_edges SET stale = ? "
            "WHERE from_id = ? AND to_project = ? AND to_entity_id = ?",
            (1 if stale else 0, from_id, to_project, to_entity_id))
        self._conn.commit()

    def read_cross_edges(self) -> Dict[str, List[dict]]:
        cur = self._conn.execute(
            "SELECT from_id, to_project, to_entity_id, stale FROM cross_edges")
        result: Dict[str, List[dict]] = {}
        for row in cur:
            result.setdefault(row[0], []).append({
                "to_project": row[1],
                "to_entity_id": row[2],
                "stale": bool(row[3]),
            })
        return result
