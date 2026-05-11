"""SQLite schema module — 将数据库结构作为 storage 虚子图暴露。"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from storage.stores.base import MatchQuery, StoreModule
from storage.stores.utils import db as db_utils


def _normalize_type(sql_type: str) -> str:
    sql_type_upper = (sql_type or "").upper()
    if any(t in sql_type_upper for t in ["INT", "SERIAL", "BIGINT"]):
        return "INT"
    if any(t in sql_type_upper for t in ["REAL", "FLOAT", "DOUBLE", "DECIMAL"]):
        return "REAL"
    if any(t in sql_type_upper for t in ["TEXT", "CLOB", "CHAR", "VARCHAR"]):
        return "TEXT"
    if any(t in sql_type_upper for t in ["BLOB", "BINARY"]):
        return "BLOB"
    if "JSON" in sql_type_upper:
        return "JSON"
    if "BOOLEAN" in sql_type_upper or "BOOL" in sql_type_upper:
        return "BOOL"
    if any(t in sql_type_upper for t in ["DATE", "TIME"]):
        return "DATETIME"
    return "TEXT"


def _node_copy(node: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in node.items()}


class SQLiteSchemaModule(StoreModule):
    name = "db_schema"

    def __init__(self, store):
        self.store = store
        self._bundle_cache: Dict[str, tuple[tuple[int, int], dict]] = {}

    def iter_virtual_nodes(self) -> list[dict]:
        nodes: List[dict] = []
        for db_rel in self._iter_db_files():
            bundle = self._bundle_for_db(db_rel)
            nodes.extend(_node_copy(n) for n in bundle["nodes"])
        return nodes

    def iter_virtual_edges(self, nodes: list[dict]) -> list[tuple[str, str]]:
        edges: List[tuple[str, str]] = []
        for db_rel in self._iter_db_files():
            bundle = self._bundle_for_db(db_rel)
            edges.extend(list(bundle["edges"]))
        return edges

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        for db_rel in self._candidate_db_paths(key):
            bundle = self._bundle_for_db(db_rel)
            meta = bundle["by_lookup"].get(key) or bundle["by_lookup"].get(self._canonical_key(key, db_rel))
            if meta:
                return _node_copy(meta)
        return None

    def get_virtual_neighbors(self, key: str) -> list:
        for db_rel in self._candidate_db_paths(key):
            bundle = self._bundle_for_db(db_rel)
            canonical = self._canonical_key(key, db_rel)
            if canonical in bundle["neighbors"]:
                return list(bundle["neighbors"][canonical])
        return []

    def match_query(self, node: dict) -> MatchQuery | None:
        labels = set(node.get("labels", []) or [])
        ref = node.get("ref", "")
        if not ref:
            return None

        if "table" in labels:
            return MatchQuery(
                query="MATCH (t:table) WHERE t.ref = $ref RETURN t",
                params={"ref": ref},
                var="t",
            )
        if "view" in labels:
            return MatchQuery(
                query="MATCH (v:view) WHERE v.ref = $ref RETURN v",
                params={"ref": ref},
                var="v",
            )
        if "col" in labels:
            return MatchQuery(
                query="MATCH (c:col) WHERE c.ref = $ref RETURN c",
                params={"ref": ref},
                var="c",
            )
        if "fk" in labels:
            return MatchQuery(
                query="MATCH (k:fk) WHERE k.ref = $ref RETURN k",
                params={"ref": ref},
                var="k",
            )
        return None

    def meta_fallback(self, ref: str, include_props=None, _visiting=None) -> dict | None:
        meta = self.get_virtual_meta(ref)
        if not meta:
            return None
        if include_props is None:
            return meta
        result = {k: v for k, v in meta.items() if k in include_props or k in ("name", "labels", "ref")}
        return result

    def _iter_db_files(self) -> list[str]:
        project_path = self.store.project_path
        if not project_path:
            return []
        results: list[str] = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                full = os.path.join(root, fname)
                if self._is_backend_file(full):
                    continue
                if not db_utils.file_labels(fname):
                    continue
                rel = os.path.relpath(full, project_path)
                results.append(rel)
        return sorted(set(results))

    def _is_backend_file(self, fp: str) -> bool:
        backend_db = getattr(self.store, "_backend_db_path", None)
        if not backend_db:
            return False
        absp = os.path.abspath(fp)
        db_base = os.path.abspath(backend_db)
        return absp == db_base or absp.startswith(db_base + "-")

    def _bundle_for_db(self, db_rel: str) -> dict:
        full = os.path.join(self.store.project_path, db_rel)
        try:
            stat = os.stat(full)
        except OSError:
            return {"nodes": [], "edges": [], "by_lookup": {}, "neighbors": {}}
        sig = (stat.st_mtime_ns, stat.st_size)
        cached = self._bundle_cache.get(db_rel)
        if cached and cached[0] == sig:
            return cached[1]
        bundle = self._build_bundle(db_rel)
        self._bundle_cache[db_rel] = (sig, bundle)
        return bundle

    def _build_bundle(self, db_rel: str) -> dict:
        db_name = os.path.basename(db_rel)
        nodes: list[dict] = []
        edges: list[tuple[str, str]] = []
        by_lookup: dict[str, dict] = {}
        neighbors: dict[str, list[str]] = {}

        def register(node: dict, aliases: list[str] | None = None):
            nodes.append(node)
            keys = {node.get("ref")}
            for alias in aliases or []:
                keys.add(alias)
            for key in keys:
                if not key:
                    continue
                by_lookup[key] = node
                neighbors.setdefault(key, [])

        def link(a_keys: list[str], a_ref: str, b_ref: str):
            edges.append((a_ref, b_ref))
            for key in a_keys:
                neighbors.setdefault(key, []).append(b_ref)

        full = os.path.join(self.store.project_path, db_rel)
        conn = db_utils.connect_sqlite(full, readonly=True, immutable=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT name FROM sqlite_master WHERE type='view'")
            views = [row[0] for row in cur.fetchall()]

            table_pks: dict[str, str] = {}
            explicit_fk_keys: set[tuple[str, str, str, str]] = set()

            for table_name in tables:
                cur.execute(f'PRAGMA table_info("{table_name}")')
                columns = cur.fetchall()
                pk_col = next((col[1] for col in columns if col[5] == 1), None)
                table_pks[table_name] = pk_col or "rowid"
                try:
                    cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                    row_count = cur.fetchone()[0]
                except Exception:
                    row_count = None

                table_ref = f"{db_name}--{table_name}"
                tnode = {
                    "name": table_name,
                    "ref": table_ref,
                    "column_count": len(columns),
                    "primary_key": pk_col or "",
                    "labels": ["table"],
                }
                if row_count is not None:
                    tnode["row_count"] = row_count
                register(tnode, aliases=[f"{db_name}/{table_name}"])
                link([db_rel, db_name], db_rel, table_ref)

                for col in columns:
                    col_name = col[1]
                    col_type = _normalize_type(col[2])
                    col_ref = f"{table_ref}--{col_name}"
                    cnode = {
                        "name": col_name,
                        "ref": col_ref,
                        "not_null": bool(col[3]),
                        "default_value": col[4],
                        "labels": ["col", col_type],
                    }
                    register(cnode, aliases=[f"{db_name}/{table_name}/{col_name}"])
                    link([table_ref, f"{db_name}/{table_name}"], table_ref, col_ref)

                cur.execute(f'PRAGMA foreign_key_list("{table_name}")')
                for fk in cur.fetchall():
                    to_table = fk[2]
                    from_col = fk[3]
                    to_col = fk[4] or table_pks.get(to_table, "rowid")
                    explicit_fk_keys.add((table_name, from_col, to_table, to_col))
                    fk_name = f"{table_name}.{from_col}->{to_table}.{to_col}"
                    fk_ref = f"{db_name}--{fk_name}"
                    fnode = {
                        "name": fk_name,
                        "ref": fk_ref,
                        "from_table": table_name,
                        "from_column": from_col,
                        "to_table": to_table,
                        "to_column": to_col,
                        "confidence": 1.0,
                        "labels": ["fk"],
                    }
                    register(fnode, aliases=[f"{db_name}/fks/{fk_name}"])
                    link([table_ref, f"{db_name}/{table_name}"], table_ref, fk_ref)
                    link([f"{table_ref}--{from_col}", f"{db_name}/{table_name}/{from_col}"], f"{table_ref}--{from_col}", fk_ref)
                    link([f"{db_name}--{to_table}", f"{db_name}/{to_table}"], f"{db_name}--{to_table}", fk_ref)
                    link([f"{db_name}--{to_table}--{to_col}", f"{db_name}/{to_table}/{to_col}"], f"{db_name}--{to_table}--{to_col}", fk_ref)
                    neighbors.setdefault(fk_ref, []).extend([
                        table_ref,
                        f"{table_ref}--{from_col}",
                        f"{db_name}--{to_table}",
                        f"{db_name}--{to_table}--{to_col}",
                    ])

            for table_name in tables:
                cur.execute(f'PRAGMA table_info("{table_name}")')
                columns = cur.fetchall()
                for col in columns:
                    col_name = col[1]
                    if col[5]:
                        continue
                    for ref_table in tables:
                        if ref_table == table_name:
                            continue
                        expected = f"{ref_table.rstrip('s')}s_id"
                        expected_alt = f"{ref_table.rstrip('s')}_id"
                        if col_name.lower() not in {expected.lower(), expected_alt.lower()}:
                            continue
                        to_col = table_pks.get(ref_table, "rowid")
                        rel_key = (table_name, col_name, ref_table, to_col)
                        if rel_key in explicit_fk_keys:
                            break
                        fk_name = f"{table_name}.{col_name}->{ref_table}.{to_col}"
                        fk_ref = f"{db_name}--{fk_name}"
                        if fk_ref in by_lookup:
                            break
                        fnode = {
                            "name": fk_name,
                            "ref": fk_ref,
                            "from_table": table_name,
                            "from_column": col_name,
                            "to_table": ref_table,
                            "to_column": to_col,
                            "confidence": 0.7,
                            "labels": ["fk"],
                        }
                        register(fnode, aliases=[f"{db_name}/fks/{fk_name}"])
                        link([f"{db_name}--{table_name}", f"{db_name}/{table_name}"], f"{db_name}--{table_name}", fk_ref)
                        link([f"{db_name}--{table_name}--{col_name}", f"{db_name}/{table_name}/{col_name}"], f"{db_name}--{table_name}--{col_name}", fk_ref)
                        link([f"{db_name}--{ref_table}", f"{db_name}/{ref_table}"], f"{db_name}--{ref_table}", fk_ref)
                        link([f"{db_name}--{ref_table}--{to_col}", f"{db_name}/{ref_table}/{to_col}"], f"{db_name}--{ref_table}--{to_col}", fk_ref)
                        neighbors.setdefault(fk_ref, []).extend([
                            f"{db_name}--{table_name}",
                            f"{db_name}--{table_name}--{col_name}",
                            f"{db_name}--{ref_table}",
                            f"{db_name}--{ref_table}--{to_col}",
                        ])
                        break

            for view_name in views:
                cur.execute(f'PRAGMA table_info("{view_name}")')
                columns = cur.fetchall()
                try:
                    cur.execute(f'SELECT COUNT(*) FROM "{view_name}"')
                    row_count = cur.fetchone()[0]
                except Exception:
                    row_count = None
                view_ref = f"{db_name}--{view_name}"
                vnode = {
                    "name": view_name,
                    "ref": view_ref,
                    "column_count": len(columns),
                    "labels": ["view"],
                }
                if row_count is not None:
                    vnode["row_count"] = row_count
                register(vnode, aliases=[f"{db_name}/{view_name}"])
                link([db_rel, db_name], db_rel, view_ref)

                for col in columns:
                    col_name = col[1]
                    col_type = _normalize_type(col[2])
                    col_ref = f"{view_ref}--{col_name}"
                    cnode = {
                        "name": col_name,
                        "ref": col_ref,
                        "labels": ["col", col_type],
                    }
                    register(cnode, aliases=[f"{db_name}/{view_name}/{col_name}"])
                    link([view_ref, f"{db_name}/{view_name}"], view_ref, col_ref)
        finally:
            conn.close()

        return {"nodes": nodes, "edges": edges, "by_lookup": by_lookup, "neighbors": neighbors}

    def _candidate_db_paths(self, key: str) -> list[str]:
        db_files = self._iter_db_files()
        if key in db_files:
            return [key]
        if "/" in key:
            head = key.split("/", 1)[0]
            return [rel for rel in db_files if os.path.basename(rel) == head or rel == head]
        if "--" in key:
            db_name = key.split("--", 1)[0]
            matches = [rel for rel in db_files if os.path.basename(rel) == db_name or rel == db_name]
            return matches
        basename = os.path.basename(key)
        return [rel for rel in db_files if os.path.basename(rel) == basename or rel == key]

    def _canonical_key(self, key: str, db_rel: str) -> str:
        db_name = os.path.basename(db_rel)
        if key == db_rel or key == db_name:
            return key
        if "--" in key:
            if key.startswith(db_name + "--"):
                return key
            return f"{db_name}--{key.split('--', 1)[1]}"
        return key


__all__ = ["SQLiteSchemaModule"]
