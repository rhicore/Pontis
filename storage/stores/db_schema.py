"""SQLite schema module — 将数据库结构作为 storage 虚子图暴露。"""

from __future__ import annotations

import os
import sqlite3
from typing import Dict, List

from storage.stores.base import (
    CypherStatement,
    ModuleContext,
    StoreModule,
    cypher_label_clause,
)
from storage.stores.access import DbConnect


DB_FILE_LABELS = {
    ".db": ["file", "db"],
    ".sqlite": ["file", "db"],
    ".sqlite3": ["file", "db"],
    ".duckdb": ["file", "db"],
}


def _file_db_labels(path_or_name: str) -> list[str] | None:
    return DB_FILE_LABELS.get(os.path.splitext(path_or_name)[1].lower())


def _connect_sqlite(path: str, *args, readonly: bool = False,
                    immutable: bool = False, **kwargs):
    if readonly or immutable:
        qs = ["mode=ro"]
        if immutable:
            qs.append("immutable=1")
        path = f"file:{path}?{'&'.join(qs)}"
        kwargs["uri"] = True
    return sqlite3.connect(path, *args, **kwargs)


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


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _safe_row_count(cur, name: str) -> int | None:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {_quote_identifier(name)}")
        return int(cur.fetchone()[0])
    except Exception:
        return None


def _primary_key_columns(columns: list[tuple]) -> list[str]:
    return [
        col[1]
        for col in sorted((col for col in columns if col[5]), key=lambda col: col[5])
    ]


def _primary_key_display(pk_cols: list[str]) -> str:
    return ", ".join(pk_cols)


def _single_primary_key_target(pk_cols: list[str]) -> str:
    return pk_cols[0] if len(pk_cols) == 1 else "rowid"


def _node_copy(node: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in node.items()}


class SQLiteSchemaModule(StoreModule):
    name = "db_schema"
    query_labels = {"db", "table", "view", "col", "fk"}

    def __init__(self, ctx: ModuleContext):
        super().__init__(ctx)
        self._bundle_cache: Dict[str, tuple[tuple[int, int], dict]] = {}

    @property
    def project_path(self) -> str:
        return self.ctx.source.root

    def should_materialize_for_query(self, parsed, raw_query: str = "") -> bool:
        if ".db_connect" in raw_query or "._db_connect" in raw_query:
            return True
        for node in getattr(parsed, "nodes", []) or []:
            if set(getattr(node, "labels", []) or []) & self.query_labels:
                return True
        return False

    def source_fingerprint(self) -> str | None:
        if not self.project_path:
            return None
        parts = []
        for rel in self._iter_db_files():
            try:
                stat = self.ctx.source.stat(rel)
            except OSError:
                continue
            parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(parts)

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

    def cypher_statements(self) -> list[CypherStatement]:
        nodes = self.iter_virtual_nodes()
        edges = self.iter_virtual_edges(nodes)
        statements: list[CypherStatement] = []

        non_fk_nodes = [node for node in nodes if "fk" not in set(node.get("labels", []) or [])]
        fk_nodes = [node for node in nodes if "fk" in set(node.get("labels", []) or [])]

        grouped: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
        for node in non_fk_nodes:
            labels = tuple(node.get("labels", []) or [])
            key_field = "path" if node.get("path") else "_ref"
            key_value = node.get(key_field)
            if not key_value:
                continue
            props = {k: v for k, v in node.items() if k != "labels" and v is not None}
            grouped.setdefault((key_field, labels), []).append({
                key_field: key_value,
                "labels": list(labels),
                "props": props,
            })

        for (key_field, labels), rows in grouped.items():
            if key_field == "_ref":
                statements.append(CypherStatement(
                    query=(
                        "UNWIND $rows AS row "
                        "MATCH (n {ref: row._ref}) "
                        "WHERE n._ref IS NULL "
                        "SET n._ref = row._ref "
                        "REMOVE n.ref"
                    ),
                    params={"rows": rows},
                ))
            statements.append(CypherStatement(
                query=(
                    f"UNWIND $rows AS row "
                    f"MERGE (n {{{key_field}: row.{key_field}}}) "
                    "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
                    "ON MATCH SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)) "
                    "SET n += row.props "
                    "REMOVE n.src, n.ref, n.db_handle, n.db_connect "
                    "WITH n, row.labels AS labels "
                    "SET n.labels = reduce(acc = [], label IN coalesce(n.labels, []) + labels | "
                    "CASE WHEN label IN acc THEN acc ELSE acc + label END) "
                    f"SET n{cypher_label_clause(list(labels))}"
                ),
                params={"rows": rows},
            ))

        fk_rows = []
        for node in fk_nodes:
            props = {k: v for k, v in node.items() if k != "labels" and v is not None}
            from_col_ref = node.get("_from_col_ref")
            to_col_ref = node.get("_to_col_ref")
            if not from_col_ref or not to_col_ref:
                continue
            fk_rows.append({
                "from_col_ref": from_col_ref,
                "to_col_ref": to_col_ref,
                "labels": list(node.get("labels", []) or []),
                "props": props,
            })
        if fk_rows:
            statements.append(CypherStatement(
                query=(
                    "UNWIND $rows AS row "
                    "MATCH (fk:fk {from_col_ref: row.from_col_ref, to_col_ref: row.to_col_ref}) "
                    "WHERE fk._from_col_ref IS NULL OR fk._to_col_ref IS NULL "
                    "SET fk._from_col_ref = row.from_col_ref, "
                    "fk._to_col_ref = row.to_col_ref, "
                    "fk._ref = coalesce(fk._ref, fk.ref, row.props._ref) "
                    "REMOVE fk.ref, fk.from_col_ref, fk.to_col_ref"
                ),
                params={"rows": fk_rows},
            ))
            statements.append(CypherStatement(
                query=(
                    "UNWIND $rows AS row "
                    "MATCH (from_col) WHERE from_col._ref = row.from_col_ref OR from_col.ref = row.from_col_ref "
                    "MATCH (to_col) WHERE to_col._ref = row.to_col_ref OR to_col.ref = row.to_col_ref "
                    "MERGE (fk:fk {_from_col_ref: row.from_col_ref, _to_col_ref: row.to_col_ref}) "
                    "ON CREATE SET fk.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
                    "ON MATCH SET fk.id = coalesce(fk.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)) "
                    "SET fk += row.props "
                    "REMOVE fk.src, fk.ref, fk.from_col_ref, fk.to_col_ref, fk.db_handle, fk.db_connect "
                    "WITH fk, from_col, to_col, row.labels AS labels "
                    "SET fk.labels = reduce(acc = [], label IN coalesce(fk.labels, []) + labels | "
                    "CASE WHEN label IN acc THEN acc ELSE acc + label END) "
                    "MERGE (from_col)-[:RELATED_TO]->(fk) "
                    "MERGE (to_col)-[:RELATED_TO]->(fk)"
                ),
                params={"rows": fk_rows},
            ))

        if edges:
            statements.append(CypherStatement(
                query=(
                    "UNWIND $edges AS edge "
                    "MATCH (a {_ref: edge.a}) "
                    "MATCH (b {_ref: edge.b}) "
                    "MERGE (a)-[:RELATED_TO]->(b)"
                ),
                params={
                    "edges": [{"a": a, "b": b} for a, b in edges],
                },
            ))

        return statements

    def _iter_db_files(self) -> list[str]:
        project_path = self.project_path
        if not project_path:
            return []
        results: list[str] = []
        for root, dirs, files in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                if not _file_db_labels(fname):
                    continue
                rel = os.path.join(root, fname) if root else fname
                results.append(rel)
        return sorted(set(results))

    def _bundle_for_db(self, db_rel: str) -> dict:
        try:
            stat = self.ctx.source.stat(db_rel)
        except OSError:
            return {"nodes": [], "edges": []}
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
        seen_refs: set[str] = set()

        def register(node: dict):
            nodes.append(node)
            ref = node.get("_ref")
            if ref:
                seen_refs.add(ref)

        def link(a_ref: str, b_ref: str):
            edges.append((a_ref, b_ref))

        full = self.ctx.source.absolute_path(db_rel)
        conn = _connect_sqlite(full, readonly=True, immutable=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT name FROM sqlite_master WHERE type='view'")
            views = [row[0] for row in cur.fetchall()]
            try:
                cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
                index_count = cur.fetchone()[0]
            except Exception:
                index_count = None

            db_node = {
                "name": db_name,
                "path": db_rel,
                "_ref": db_rel,
                "_db_connect": self.pointer("connect", db_rel),
                "table_count": len(tables),
                "view_count": len(views),
                "labels": ["file", "db"],
            }
            if index_count is not None:
                db_node["index_count"] = index_count
            register(db_node)

            table_pks: dict[str, str] = {}
            explicit_fk_keys: set[tuple[str, str, str, str]] = set()

            for table_name in tables:
                cur.execute(f'PRAGMA table_info("{table_name}")')
                columns = cur.fetchall()
                pk_cols = _primary_key_columns(columns)
                table_pks[table_name] = _single_primary_key_target(pk_cols)
                row_count = _safe_row_count(cur, table_name)

                table_ref = f"{db_name}--{table_name}"
                tnode = {
                    "name": table_name,
                    "_ref": table_ref,
                    "_db_ref": db_rel,
                    "_db_connect": self.pointer("connect", db_rel),
                    "table_name": table_name,
                    "column_count": len(columns),
                    "primary_key": _primary_key_display(pk_cols),
                    "labels": ["table"],
                }
                if row_count is not None:
                    tnode["row_count"] = row_count
                register(tnode)
                link(db_rel, table_ref)

                for col in columns:
                    col_name = col[1]
                    col_type = _normalize_type(col[2])
                    col_ref = f"{table_ref}--{col_name}"
                    cnode = {
                        "name": col_name,
                        "_ref": col_ref,
                        "_db_ref": db_rel,
                        "_db_connect": self.pointer("connect", db_rel),
                        "table_name": table_name,
                        "column_name": col_name,
                        "not_null": bool(col[3]),
                        "default_value": col[4],
                        "labels": ["col", col_type],
                    }
                    register(cnode)
                    link(table_ref, col_ref)

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
                        "_ref": fk_ref,
                        "_db_ref": db_rel,
                        "_db_connect": self.pointer("connect", db_rel),
                        "from_table": table_name,
                        "from_column": from_col,
                        "_from_col_ref": f"{table_ref}--{from_col}",
                        "to_table": to_table,
                        "to_column": to_col,
                        "_to_col_ref": f"{db_name}--{to_table}--{to_col}",
                        "confidence": 1.0,
                        "labels": ["fk"],
                    }
                    register(fnode)
                    link(db_rel, fk_ref)
                    link(table_ref, fk_ref)
                    link(f"{table_ref}--{from_col}", fk_ref)
                    link(f"{db_name}--{to_table}", fk_ref)
                    link(f"{db_name}--{to_table}--{to_col}", fk_ref)

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
                        if fk_ref in seen_refs:
                            break
                        fnode = {
                            "name": fk_name,
                            "_ref": fk_ref,
                            "_db_ref": db_rel,
                            "_db_connect": self.pointer("connect", db_rel),
                            "from_table": table_name,
                            "from_column": col_name,
                            "_from_col_ref": f"{db_name}--{table_name}--{col_name}",
                            "to_table": ref_table,
                            "to_column": to_col,
                            "_to_col_ref": f"{db_name}--{ref_table}--{to_col}",
                            "confidence": 0.7,
                            "labels": ["fk"],
                        }
                        register(fnode)
                        link(db_rel, fk_ref)
                        link(f"{db_name}--{table_name}", fk_ref)
                        link(f"{db_name}--{table_name}--{col_name}", fk_ref)
                        link(f"{db_name}--{ref_table}", fk_ref)
                        link(f"{db_name}--{ref_table}--{to_col}", fk_ref)
                        break

            for view_name in views:
                cur.execute(f'PRAGMA table_info("{view_name}")')
                columns = cur.fetchall()
                row_count = _safe_row_count(cur, view_name)
                view_ref = f"{db_name}--{view_name}"
                vnode = {
                    "name": view_name,
                    "_ref": view_ref,
                    "_db_ref": db_rel,
                    "_db_connect": self.pointer("connect", db_rel),
                    "view_name": view_name,
                    "column_count": len(columns),
                    "labels": ["view"],
                }
                if row_count is not None:
                    vnode["row_count"] = row_count
                register(vnode)
                link(db_rel, view_ref)

                for col in columns:
                    col_name = col[1]
                    col_type = _normalize_type(col[2])
                    col_ref = f"{view_ref}--{col_name}"
                    cnode = {
                        "name": col_name,
                        "_ref": col_ref,
                        "_db_ref": db_rel,
                        "_db_connect": self.pointer("connect", db_rel),
                        "table_name": view_name,
                        "column_name": col_name,
                        "labels": ["col", col_type],
                    }
                    register(cnode)
                    link(view_ref, col_ref)
        finally:
            conn.close()

        return {"nodes": nodes, "edges": edges}

    def resolve_pointer(self, kind: str, payload: str, *, node: dict | None = None):
        if kind != "connect":
            return None
        db_rel = payload
        abs_path = self.ctx.source.absolute_path(db_rel)
        if not os.path.isfile(abs_path):
            return None

        node_data = dict(node or {})
        return DbConnect(
            db_path=abs_path,
            connect=lambda *args, **kwargs: _connect_sqlite(abs_path, *args, **kwargs),
            table=node_data.get("table_name", ""),
            view=node_data.get("view_name", ""),
            column=node_data.get("column_name", ""),
            fk=node_data.get("name", "") if "fk" in set(node_data.get("labels", []) or []) else "",
        )

__all__ = ["SQLiteSchemaModule"]
