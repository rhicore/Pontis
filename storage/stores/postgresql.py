"""PostgreSQL source module.

This module exposes a live PostgreSQL database as Pontis schema facts. A
Docker-hosted PostgreSQL service is just one deployment shape; Pontis connects
through normal host/port credentials configured on the source.
"""

from __future__ import annotations

import json
import os
from typing import Any

from storage.stores.access import DbConnect
from storage.stores.base import (
    CypherStatement,
    ModuleContext,
    StoreModule,
    cypher_label_clause,
)


def _normalize_type(sql_type: str) -> str:
    sql_type_upper = (sql_type or "").upper()
    if any(t in sql_type_upper for t in ["INT", "SERIAL", "BIGSERIAL", "SMALLSERIAL"]):
        return "INT"
    if any(t in sql_type_upper for t in ["REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "MONEY"]):
        return "REAL"
    if any(t in sql_type_upper for t in ["TEXT", "CHAR", "VARCHAR", "UUID", "INET", "CIDR", "MACADDR"]):
        return "TEXT"
    if any(t in sql_type_upper for t in ["BYTEA", "BINARY"]):
        return "BLOB"
    if any(t in sql_type_upper for t in ["JSON", "ARRAY", "RANGE"]):
        return "JSON"
    if "BOOL" in sql_type_upper:
        return "BOOL"
    if any(t in sql_type_upper for t in ["DATE", "TIME", "TIMESTAMP", "INTERVAL"]):
        return "DATETIME"
    return "TEXT"


def _clean_identifier(value: str) -> str:
    return str(value or "").strip().strip('"')


class PostgreSQLSchemaModule(StoreModule):
    name = "postgresql"
    query_labels = {"db", "schema", "table", "view", "col", "fk", "postgresql"}
    refresh_interval_seconds = 3600.0

    def __init__(self, ctx: ModuleContext):
        super().__init__(ctx)
        self._bundle_cache: dict[str, Any] | None = None

    @property
    def database(self) -> str:
        return _clean_identifier(getattr(self.ctx.source_config, "database", "") or self.project_name)

    @property
    def schema_filter(self) -> str:
        return _clean_identifier(getattr(self.ctx.source_config, "schema", ""))

    def should_materialize_for_query(self, parsed, raw_query: str = "") -> bool:
        if ".db_connect" in raw_query or "._db_connect" in raw_query:
            return True
        for node in getattr(parsed, "nodes", []) or []:
            if set(getattr(node, "labels", []) or []) & self.query_labels:
                return True
        return False

    def source_fingerprint(self) -> str | None:
        source = self.ctx.source_config
        return "|".join([
            "postgresql",
            getattr(source, "host", "") or "",
            str(getattr(source, "port", 0) or ""),
            self.database,
            self.schema_filter,
            getattr(source, "user", "") or getattr(source, "username", "") or "",
        ])

    def iter_virtual_nodes(self) -> list[dict]:
        return [dict(node) for node in self._bundle()["nodes"]]

    def iter_virtual_edges(self, nodes: list[dict]) -> list[tuple[str, str]]:
        return list(self._bundle()["edges"])

    def cypher_statements(self) -> list[CypherStatement]:
        nodes = self.iter_virtual_nodes()
        edges = self.iter_virtual_edges(nodes)
        statements: list[CypherStatement] = []

        grouped: dict[tuple[str, ...], list[dict]] = {}
        for node in nodes:
            labels = tuple(node.get("labels", []) or [])
            ref = node.get("_ref")
            if not ref:
                continue
            props = {k: v for k, v in node.items() if k != "labels" and v is not None}
            grouped.setdefault(labels, []).append({
                "_ref": ref,
                "labels": list(labels),
                "props": props,
            })

        for labels, rows in grouped.items():
            statements.append(CypherStatement(
                query=(
                    "UNWIND $rows AS row "
                    "MERGE (n {_ref: row._ref}) "
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

        if edges:
            statements.append(CypherStatement(
                query=(
                    "UNWIND $edges AS edge "
                    "MATCH (a {_ref: edge.a}) "
                    "MATCH (b {_ref: edge.b}) "
                    "MERGE (a)-[:RELATED_TO]->(b)"
                ),
                params={"edges": [{"a": a, "b": b} for a, b in edges]},
            ))

        return statements

    def resolve_pointer(self, kind: str, payload: str, *, node: dict | None = None):
        if kind != "connect":
            return None
        node_data = dict(node or {})
        return DbConnect(
            db_path=self.database,
            connect=lambda *args, **kwargs: self.connect(*args, **kwargs),
            dialect="postgresql",
            table=node_data.get("table_name", ""),
            view=node_data.get("view_name", ""),
            column=node_data.get("column_name", ""),
            fk=node_data.get("name", "") if "fk" in set(node_data.get("labels", []) or []) else "",
        )

    def connect(self, *args, **kwargs):
        readonly = bool(kwargs.pop("readonly", False))
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required for source.type=postgresql") from exc

        conn = psycopg2.connect(*args, **self._connection_kwargs(), **kwargs)
        if readonly:
            conn.set_session(readonly=True, autocommit=True)
        return conn

    def _bundle(self) -> dict:
        if self._bundle_cache is not None:
            return self._bundle_cache
        self._bundle_cache = self._build_bundle()
        return self._bundle_cache

    def _build_bundle(self) -> dict:
        database = self.database
        if not database:
            raise ValueError("PostgreSQL source requires source.database or project name")

        nodes: list[dict] = []
        edges: list[tuple[str, str]] = []
        db_ref = database
        nodes.append({
            "name": database,
            "path": database,
            "_ref": db_ref,
            "_db_connect": self.pointer("connect", database),
            "database_name": database,
            "dialect": "postgresql",
            "host": getattr(self.ctx.source_config, "host", "") or "",
            "port": int(getattr(self.ctx.source_config, "port", 0) or 5432),
            "labels": ["db", "postgresql"],
        })

        conn = self.connect()
        try:
            cur = conn.cursor()
            try:
                tables = self._fetch_tables(cur)
                columns = self._fetch_columns(cur)
                primary_keys = self._fetch_primary_keys(cur)
                foreign_keys = self._fetch_foreign_keys(cur)
            finally:
                cur.close()
        finally:
            conn.close()

        schemas: set[str] = set()
        table_refs: dict[tuple[str, str], str] = {}
        table_types: dict[tuple[str, str], str] = {}
        column_refs: set[tuple[str, str, str]] = set()

        for table in tables:
            schema_name = str(table["schema_name"])
            table_name = str(table["table_name"])
            schemas.add(schema_name)
            schema_ref = f"{database}--{schema_name}"
            table_ref = f"{schema_ref}--{table_name}"
            table_refs[(schema_name, table_name)] = table_ref
            table_type = str(table.get("table_type") or "")
            table_types[(schema_name, table_name)] = table_type
            is_view = "VIEW" in table_type.upper()
            pk_cols = primary_keys.get((schema_name, table_name), [])
            node = {
                "name": table_name,
                "_ref": table_ref,
                "_db_ref": db_ref,
                "_schema_ref": schema_ref,
                "_db_connect": self.pointer("connect", database),
                "database_name": database,
                "schema_name": schema_name,
                "table_name": table_name,
                "table_type": table_type,
                "row_count": table.get("row_count"),
                "primary_key": ", ".join(pk_cols),
                "labels": ["view"] if is_view else ["table"],
            }
            nodes.append(node)
            edges.append((schema_ref, table_ref))

        for schema_name in sorted(schemas):
            schema_ref = f"{database}--{schema_name}"
            nodes.append({
                "name": schema_name,
                "_ref": schema_ref,
                "_db_ref": db_ref,
                "_db_connect": self.pointer("connect", database),
                "database_name": database,
                "schema_name": schema_name,
                "labels": ["schema"],
            })
            edges.append((db_ref, schema_ref))

        for column in columns:
            schema_name = str(column["schema_name"])
            table_name = str(column["table_name"])
            col_name = str(column["column_name"])
            table_ref = table_refs.get((schema_name, table_name))
            if not table_ref:
                continue
            col_ref = f"{table_ref}--{col_name}"
            sql_type = str(column.get("data_type") or "")
            is_view = "VIEW" in table_types.get((schema_name, table_name), "").upper()
            nodes.append({
                "name": col_name,
                "_ref": col_ref,
                "_db_ref": db_ref,
                "_table_ref": table_ref,
                "_db_connect": self.pointer("connect", database),
                "database_name": database,
                "schema_name": schema_name,
                "table_name": table_name,
                "view_name": table_name if is_view else "",
                "column_name": col_name,
                "ordinal_position": column.get("ordinal_position"),
                "data_type": sql_type,
                "not_null": str(column.get("is_nullable") or "").upper() == "NO",
                "default_value": column.get("column_default"),
                "character_maximum_length": column.get("character_maximum_length"),
                "numeric_precision": column.get("numeric_precision"),
                "numeric_scale": column.get("numeric_scale"),
                "labels": ["col", _normalize_type(sql_type)],
            })
            column_refs.add((schema_name, table_name, col_name))
            edges.append((table_ref, col_ref))

        for fk in foreign_keys:
            from_key = (fk["from_schema"], fk["from_table"])
            to_key = (fk["to_schema"], fk["to_table"])
            from_table_ref = table_refs.get(from_key)
            to_table_ref = table_refs.get(to_key)
            from_col_key = (*from_key, fk["from_column"])
            to_col_key = (*to_key, fk["to_column"])
            if not from_table_ref or not to_table_ref:
                continue
            if from_col_key not in column_refs or to_col_key not in column_refs:
                continue
            from_col_ref = f"{from_table_ref}--{fk['from_column']}"
            to_col_ref = f"{to_table_ref}--{fk['to_column']}"
            fk_name = (
                f"{fk['from_schema']}.{fk['from_table']}.{fk['from_column']}"
                f"->{fk['to_schema']}.{fk['to_table']}.{fk['to_column']}"
            )
            fk_ref = f"{database}--fk--{fk_name}"
            nodes.append({
                "name": fk_name,
                "_ref": fk_ref,
                "_db_ref": db_ref,
                "_db_connect": self.pointer("connect", database),
                "constraint_name": fk.get("constraint_name"),
                "from_schema": fk["from_schema"],
                "from_table": fk["from_table"],
                "from_column": fk["from_column"],
                "_from_col_ref": from_col_ref,
                "to_schema": fk["to_schema"],
                "to_table": fk["to_table"],
                "to_column": fk["to_column"],
                "_to_col_ref": to_col_ref,
                "confidence": 1.0,
                "labels": ["fk"],
            })
            edges.extend([
                (db_ref, fk_ref),
                (from_table_ref, fk_ref),
                (from_col_ref, fk_ref),
                (to_table_ref, fk_ref),
                (to_col_ref, fk_ref),
            ])

        return {"nodes": nodes, "edges": edges}

    def _schema_clause(self, column: str = "n.nspname") -> tuple[str, list[Any]]:
        schema_filter = self.schema_filter
        if schema_filter:
            return f" AND {column} = %s", [schema_filter]
        return "", []

    def _fetch_tables(self, cur) -> list[dict]:
        schema_sql, params = self._schema_clause("n.nspname")
        cur.execute(
            (
                "SELECT n.nspname AS schema_name, c.relname AS table_name, "
                "CASE c.relkind "
                "WHEN 'v' THEN 'VIEW' "
                "WHEN 'm' THEN 'MATERIALIZED VIEW' "
                "WHEN 'p' THEN 'PARTITIONED TABLE' "
                "ELSE 'BASE TABLE' END AS table_type, "
                "CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint ELSE NULL END AS row_count "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p', 'v', 'm') "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND n.nspname NOT LIKE 'pg_toast%%' "
                f"{schema_sql} "
                "ORDER BY n.nspname, c.relname"
            ),
            params,
        )
        return [
            {
                "schema_name": row[0],
                "table_name": row[1],
                "table_type": row[2],
                "row_count": row[3],
            }
            for row in cur.fetchall()
        ]

    def _fetch_columns(self, cur) -> list[dict]:
        schema_filter = self.schema_filter
        sql = (
            "SELECT table_schema, table_name, column_name, ordinal_position, "
            "data_type, is_nullable, column_default, character_maximum_length, "
            "numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "AND table_schema NOT LIKE 'pg_toast%%'"
        )
        params: list[Any] = []
        if schema_filter:
            sql += " AND table_schema = %s"
            params.append(schema_filter)
        sql += " ORDER BY table_schema, table_name, ordinal_position"
        cur.execute(sql, params)
        return [
            {
                "schema_name": row[0],
                "table_name": row[1],
                "column_name": row[2],
                "ordinal_position": row[3],
                "data_type": row[4],
                "is_nullable": row[5],
                "column_default": row[6],
                "character_maximum_length": row[7],
                "numeric_precision": row[8],
                "numeric_scale": row[9],
            }
            for row in cur.fetchall()
        ]

    def _fetch_primary_keys(self, cur) -> dict[tuple[str, str], list[str]]:
        schema_sql, params = self._schema_clause("ns.nspname")
        cur.execute(
            (
                "SELECT ns.nspname, cls.relname, att.attname, ord.ordinality "
                "FROM pg_index idx "
                "JOIN pg_class cls ON cls.oid = idx.indrelid "
                "JOIN pg_namespace ns ON ns.oid = cls.relnamespace "
                "JOIN unnest(idx.indkey) WITH ORDINALITY AS ord(attnum, ordinality) ON true "
                "JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = ord.attnum "
                "WHERE idx.indisprimary "
                "AND ns.nspname NOT IN ('pg_catalog', 'information_schema') "
                f"{schema_sql} "
                "ORDER BY ns.nspname, cls.relname, ord.ordinality"
            ),
            params,
        )
        primary_keys: dict[tuple[str, str], list[str]] = {}
        for schema_name, table_name, col_name, _ordinality in cur.fetchall():
            primary_keys.setdefault((schema_name, table_name), []).append(col_name)
        return primary_keys

    def _fetch_foreign_keys(self, cur) -> list[dict]:
        schema_sql, params = self._schema_clause("src_ns.nspname")
        cur.execute(
            (
                "SELECT con.conname, "
                "src_ns.nspname AS from_schema, src_cls.relname AS from_table, src_att.attname AS from_column, "
                "dst_ns.nspname AS to_schema, dst_cls.relname AS to_table, dst_att.attname AS to_column, "
                "src_ord.ordinality "
                "FROM pg_constraint con "
                "JOIN pg_class src_cls ON src_cls.oid = con.conrelid "
                "JOIN pg_namespace src_ns ON src_ns.oid = src_cls.relnamespace "
                "JOIN pg_class dst_cls ON dst_cls.oid = con.confrelid "
                "JOIN pg_namespace dst_ns ON dst_ns.oid = dst_cls.relnamespace "
                "JOIN unnest(con.conkey) WITH ORDINALITY AS src_ord(attnum, ordinality) ON true "
                "JOIN unnest(con.confkey) WITH ORDINALITY AS dst_ord(attnum, ordinality) "
                "ON dst_ord.ordinality = src_ord.ordinality "
                "JOIN pg_attribute src_att ON src_att.attrelid = src_cls.oid AND src_att.attnum = src_ord.attnum "
                "JOIN pg_attribute dst_att ON dst_att.attrelid = dst_cls.oid AND dst_att.attnum = dst_ord.attnum "
                "WHERE con.contype = 'f' "
                "AND src_ns.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND dst_ns.nspname NOT IN ('pg_catalog', 'information_schema') "
                f"{schema_sql} "
                "ORDER BY src_ns.nspname, src_cls.relname, con.conname, src_ord.ordinality"
            ),
            params,
        )
        return [
            {
                "constraint_name": row[0],
                "from_schema": row[1],
                "from_table": row[2],
                "from_column": row[3],
                "to_schema": row[4],
                "to_table": row[5],
                "to_column": row[6],
            }
            for row in cur.fetchall()
        ]

    def _connection_kwargs(self) -> dict:
        source = self.ctx.source_config
        kwargs: dict[str, Any] = {}
        credential_path = getattr(source, "credential_path", "") or ""
        if credential_path:
            with open(credential_path, "r", encoding="utf-8") as fh:
                kwargs.update(json.load(fh))

        for key in ("host", "database", "user", "username", "password", "sslmode", "connect_timeout"):
            value = getattr(source, key, "") or ""
            if value:
                kwargs[key] = value

        port = int(getattr(source, "port", 0) or 0)
        if port:
            kwargs["port"] = port
        if kwargs.get("username") and not kwargs.get("user"):
            kwargs["user"] = kwargs.pop("username")
        else:
            kwargs.pop("username", None)

        password_env = getattr(source, "password_env", "") or ""
        if password_env:
            kwargs["password"] = os.environ.get(password_env, "")

        kwargs["dbname"] = kwargs.pop("database", None) or self.database
        if not kwargs.get("host"):
            kwargs["host"] = "localhost"
        if not kwargs.get("port"):
            kwargs["port"] = 5432
        return kwargs


__all__ = ["PostgreSQLSchemaModule"]
