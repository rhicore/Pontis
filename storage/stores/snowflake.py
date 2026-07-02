"""Snowflake source module.

This module exposes a live Snowflake database as Pontis schema facts:
database, schemas, tables/views, and columns. It is generic storage plumbing;
benchmark-specific submission and gold-result comparison belongs outside
storage.
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
    if any(t in sql_type_upper for t in ["INT", "NUMBER", "NUMERIC"]):
        return "INT"
    if any(t in sql_type_upper for t in ["REAL", "FLOAT", "DOUBLE", "DECIMAL"]):
        return "REAL"
    if any(t in sql_type_upper for t in ["TEXT", "CHAR", "VARCHAR", "STRING"]):
        return "TEXT"
    if any(t in sql_type_upper for t in ["BINARY", "VARBINARY"]):
        return "BLOB"
    if any(t in sql_type_upper for t in ["VARIANT", "OBJECT", "ARRAY", "JSON"]):
        return "JSON"
    if "BOOL" in sql_type_upper:
        return "BOOL"
    if any(t in sql_type_upper for t in ["DATE", "TIME", "TIMESTAMP"]):
        return "DATETIME"
    return "TEXT"


def _clean_identifier(value: str) -> str:
    return str(value or "").strip().strip('"')


class SnowflakeSchemaModule(StoreModule):
    name = "snowflake"
    query_labels = {"db", "schema", "table", "view", "col", "snowflake"}
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
        return "|".join([
            "snowflake",
            self.database,
            self.schema_filter,
            getattr(self.ctx.source_config, "account", "") or "",
            getattr(self.ctx.source_config, "warehouse", "") or "",
            getattr(self.ctx.source_config, "role", "") or "",
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
            dialect="snowflake",
            table=node_data.get("table_name", ""),
            view=node_data.get("view_name", ""),
            column=node_data.get("column_name", ""),
        )

    def connect(self, *args, **kwargs):
        readonly = kwargs.pop("readonly", None)
        if readonly is not None:
            # Kept for compatibility with SQLite callers. Snowflake permissions
            # and the query tool's SELECT-only check enforce read-only use.
            pass
        try:
            import snowflake.connector
        except ImportError as exc:
            raise RuntimeError(
                "snowflake-connector-python is required for source.type=snowflake"
            ) from exc
        return snowflake.connector.connect(*args, **self._connection_kwargs(), **kwargs)

    def _bundle(self) -> dict:
        if self._bundle_cache is not None:
            return self._bundle_cache
        self._bundle_cache = self._build_bundle()
        return self._bundle_cache

    def _build_bundle(self) -> dict:
        database = self.database
        if not database:
            raise ValueError("Snowflake source requires source.database or project name")

        nodes: list[dict] = []
        edges: list[tuple[str, str]] = []
        db_ref = database
        nodes.append({
            "name": database,
            "path": database,
            "_ref": db_ref,
            "_db_connect": self.pointer("connect", database),
            "database_name": database,
            "dialect": "snowflake",
            "labels": ["db", "snowflake"],
        })

        conn = self.connect()
        try:
            cur = conn.cursor()
            try:
                tables = self._fetch_tables(cur)
                columns = self._fetch_columns(cur)
            finally:
                cur.close()
        finally:
            conn.close()

        schemas: set[str] = set()
        table_refs: dict[tuple[str, str], str] = {}
        for table in tables:
            schema_name = str(table["schema_name"])
            table_name = str(table["table_name"])
            schemas.add(schema_name)
            schema_ref = f"{database}--{schema_name}"
            table_ref = f"{schema_ref}--{table_name}"
            table_refs[(schema_name, table_name)] = table_ref
            is_view = "VIEW" in str(table.get("table_type") or "").upper()
            node = {
                "name": table_name,
                "_ref": table_ref,
                "_db_ref": db_ref,
                "_schema_ref": schema_ref,
                "_db_connect": self.pointer("connect", database),
                "database_name": database,
                "schema_name": schema_name,
                "table_name": table_name,
                "table_type": table.get("table_type"),
                "row_count": table.get("row_count"),
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
            nodes.append({
                "name": col_name,
                "_ref": col_ref,
                "_db_ref": db_ref,
                "_table_ref": table_ref,
                "_db_connect": self.pointer("connect", database),
                "database_name": database,
                "schema_name": schema_name,
                "table_name": table_name,
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
            edges.append((table_ref, col_ref))

        return {"nodes": nodes, "edges": edges}

    def _fetch_tables(self, cur) -> list[dict]:
        schema_filter = self.schema_filter
        sql = (
            "SELECT table_schema, table_name, table_type, row_count "
            "FROM information_schema.tables "
            "WHERE table_catalog = CURRENT_DATABASE() "
            "AND table_schema <> 'INFORMATION_SCHEMA'"
        )
        params: list[Any] = []
        if schema_filter:
            sql += " AND table_schema = %s"
            params.append(schema_filter)
        sql += " ORDER BY table_schema, table_name"
        cur.execute(sql, params)
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
            "WHERE table_catalog = CURRENT_DATABASE() "
            "AND table_schema <> 'INFORMATION_SCHEMA'"
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

    def _connection_kwargs(self) -> dict:
        source = self.ctx.source_config
        kwargs: dict[str, Any] = {}
        credential_path = getattr(source, "credential_path", "") or ""
        if credential_path:
            with open(credential_path, "r", encoding="utf-8") as fh:
                kwargs.update(json.load(fh))

        for key in ("account", "user", "username", "password", "role", "warehouse"):
            value = getattr(source, key, "") or ""
            if value:
                kwargs[key] = value

        if kwargs.get("username") and not kwargs.get("user"):
            kwargs["user"] = kwargs.pop("username")
        else:
            kwargs.pop("username", None)

        password_env = getattr(source, "password_env", "") or ""
        if password_env:
            kwargs["password"] = os.environ.get(password_env, "")

        kwargs["database"] = self.database
        if self.schema_filter:
            kwargs["schema"] = self.schema_filter
        return kwargs


__all__ = ["SnowflakeSchemaModule"]
