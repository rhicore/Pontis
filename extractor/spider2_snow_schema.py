"""Spider2-Snow official schema extractor.

This pass imports only the official files under ``database/``:
``<schema>/DDL.csv`` and ``<schema>/*.json``. External markdown documents are
question-level context and are intentionally excluded from this extractor.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from extractor.utils.refs import neo4j_props
from extractor.utils.semantic_domain import classify_semantic_domain
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

_TYPE_LABELS = {"INT", "REAL", "TEXT", "BLOB", "BOOL", "DATETIME", "JSON", "FLOAT"}
_CONSTRAINT_START_RE = re.compile(r"^(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|KEY)\b", re.I)
_CREATE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TRANSIENT\s+|TEMPORARY\s+|TEMP\s+)?"
    r"(TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.$\"]+)",
    re.I,
)
_TABLE_PK_RE = re.compile(r"(?:CONSTRAINT\s+([A-Za-z0-9_\"]+)\s+)?PRIMARY\s+KEY\s*\((.*?)\)", re.I | re.S)
_FK_RE = re.compile(
    r"(?:CONSTRAINT\s+([A-Za-z0-9_\"]+)\s+)?FOREIGN\s+KEY\s*\((.*?)\)\s+"
    r"REFERENCES\s+([A-Za-z0-9_.$\"]+)\s*(?:\((.*?)\))?",
    re.I | re.S,
)
_DEFAULT_RE = re.compile(
    r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT\s+NULL|NULL|PRIMARY\s+KEY|UNIQUE|REFERENCES|COMMENT|COLLATE)\b|$)",
    re.I | re.S,
)
_MAX_SAMPLE_VALUES = 3
_MAX_SAMPLE_CHARS = 512
_UPSERT_BATCH_SIZE = 200


@dataclass
class ColumnMeta:
    name: str
    ordinal_position: int
    data_type: str = ""
    not_null: bool | None = None
    default_value: str = ""
    official_column_description: str = ""
    sample: list[str] = field(default_factory=list)


@dataclass
class RelationMeta:
    name: str
    kind: str = "table"
    official_description: str = ""
    ddl: str = ""
    primary_key: str = ""
    columns: dict[str, ColumnMeta] = field(default_factory=dict)


@dataclass
class ForeignKeyMeta:
    source_table: str
    source_columns: list[str]
    target_table: str
    target_columns: list[str]
    constraint_name: str = ""


def generate(workspace: Workspace) -> None:
    """Write official Spider2-Snow schema metadata to the project graph."""

    project_path = Path(workspace.project_path)
    database_dir = _database_dir(project_path)
    if not database_dir.is_dir():
        logger.info("=== Spider2-Snow schema extract: no database directory ===")
        return

    db_id = _database_id(project_path, database_dir)
    logger.info("=== Spider2-Snow official schema extract: %s ===", db_id)

    schemas, relations, foreign_keys = _load_official_schema(database_dir, db_id)
    _delete_existing_schema(workspace)
    _write_schema(workspace, db_id, schemas, relations, foreign_keys)

    table_count = sum(1 for rel in relations.values() if rel.kind == "table")
    view_count = sum(1 for rel in relations.values() if rel.kind == "view")
    column_count = sum(len(rel.columns) for rel in relations.values())
    logger.info(
        "  Written: %s schemas, %s tables, %s views, %s columns, %s foreign keys",
        len(schemas),
        table_count,
        view_count,
        column_count,
        len(foreign_keys),
    )


def _database_dir(project_path: Path) -> Path:
    copied = project_path / "database"
    if copied.is_dir():
        return copied
    return project_path


def _database_id(project_path: Path, database_dir: Path) -> str:
    if database_dir == project_path / "database":
        return project_path.name
    return database_dir.name


def _load_official_schema(
    database_dir: Path,
    db_id: str,
) -> tuple[set[str], dict[tuple[str, str], RelationMeta], list[ForeignKeyMeta]]:
    schemas: set[str] = set()
    relations: dict[tuple[str, str], RelationMeta] = {}
    foreign_keys: list[ForeignKeyMeta] = []

    for schema_dir in sorted(path for path in database_dir.iterdir() if path.is_dir()):
        schema_name = schema_dir.name
        schemas.add(schema_name)
        ddl_path = schema_dir / "DDL.csv"
        if ddl_path.exists():
            _read_ddl_csv(ddl_path, db_id, schema_name, relations, foreign_keys)
        for json_path in sorted(schema_dir.glob("*.json")):
            _merge_table_json(json_path, db_id, schema_name, relations)

    return schemas, relations, foreign_keys


def _read_ddl_csv(
    ddl_path: Path,
    db_id: str,
    default_schema: str,
    relations: dict[tuple[str, str], RelationMeta],
    foreign_keys: list[ForeignKeyMeta],
) -> None:
    with ddl_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_name = _clean(row.get("table_name"))
            if not raw_name:
                continue
            schema_name, relation_name = _split_relation_name(raw_name, db_id, default_schema)
            key = (schema_name, relation_name.upper())
            ddl = _clean(row.get("DDL"))
            parsed = _parse_ddl(ddl, fallback_name=relation_name)
            rel = relations.setdefault(key, RelationMeta(name=relation_name))
            rel.name = relation_name
            rel.kind = parsed.get("kind") or rel.kind
            rel.official_description = _clean(row.get("description")) or rel.official_description
            rel.ddl = ddl or rel.ddl
            if parsed.get("primary_key"):
                rel.primary_key = ", ".join(parsed["primary_key"])
            for col in parsed.get("columns", []):
                _merge_column(rel, col)
            for fk in parsed.get("foreign_keys", []):
                foreign_keys.append(ForeignKeyMeta(
                    source_table=relation_name,
                    source_columns=fk.get("source_columns", []),
                    target_table=fk.get("target_table", ""),
                    target_columns=fk.get("target_columns", []),
                    constraint_name=fk.get("constraint_name", ""),
                ))


def _merge_table_json(
    json_path: Path,
    db_id: str,
    default_schema: str,
    relations: dict[tuple[str, str], RelationMeta],
) -> None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read Spider2 table JSON: %s", json_path)
        return

    full_name = _clean(data.get("table_fullname"))
    table_name = _clean(data.get("table_name")) or json_path.stem
    if full_name:
        schema_name, relation_name = _split_relation_name(full_name, db_id, default_schema)
    else:
        schema_name, relation_name = _split_relation_name(table_name, db_id, default_schema)

    key = (schema_name, relation_name.upper())
    rel = relations.setdefault(key, RelationMeta(name=relation_name))
    rel.name = relation_name

    names = [str(item) for item in data.get("column_names") or []]
    types = [str(item) for item in data.get("column_types") or []]
    descriptions = [str(item) if item is not None else "" for item in data.get("description") or []]
    sample_rows = data.get("sample_rows") or []
    samples_by_col = _samples_by_column(sample_rows)

    for idx, col_name in enumerate(names, start=1):
        col = ColumnMeta(
            name=col_name,
            ordinal_position=idx,
            data_type=types[idx - 1] if idx - 1 < len(types) else "",
            official_column_description=descriptions[idx - 1] if idx - 1 < len(descriptions) else "",
            sample=samples_by_col.get(col_name, []),
        )
        _merge_column(rel, col)


def _parse_ddl(ddl: str, *, fallback_name: str) -> dict[str, Any]:
    if not ddl:
        return {"kind": "table", "columns": [], "primary_key": [], "foreign_keys": []}

    match = _CREATE_RE.search(ddl)
    kind = "view" if match and match.group(1).upper() == "VIEW" else "table"
    body = _paren_body(ddl)
    parts = _split_top_level_csv(body) if body else []

    columns: list[ColumnMeta] = []
    primary_key: list[str] = []
    foreign_keys: list[dict] = []

    for part in parts:
        item = part.strip().rstrip(",")
        if not item:
            continue
        if _CONSTRAINT_START_RE.match(item):
            pk_match = _TABLE_PK_RE.search(item)
            if pk_match:
                primary_key.extend(_identifier_list(pk_match.group(2)))
            fk_match = _FK_RE.search(item)
            if fk_match:
                foreign_keys.append({
                    "constraint_name": _unquote(fk_match.group(1) or ""),
                    "source_columns": _identifier_list(fk_match.group(2)),
                    "target_table": _unquote(fk_match.group(3) or ""),
                    "target_columns": _identifier_list(fk_match.group(4) or ""),
                })
            continue
        col = _parse_column_def(item, len(columns) + 1)
        if col:
            columns.append(col)
            if re.search(r"\bPRIMARY\s+KEY\b", item, re.I):
                primary_key.append(col.name)

    if not columns and kind == "view":
        columns = []
    return {
        "kind": kind,
        "columns": columns,
        "primary_key": list(dict.fromkeys(primary_key)),
        "foreign_keys": foreign_keys,
        "relation_name": fallback_name,
    }


def _parse_column_def(definition: str, ordinal: int) -> ColumnMeta | None:
    name, rest = _first_identifier(definition)
    if not name or not rest:
        return None
    upper = rest.upper()
    type_text = _type_prefix(rest)
    default = ""
    default_match = _DEFAULT_RE.search(rest)
    if default_match:
        default = default_match.group(1).strip().rstrip(",")
    return ColumnMeta(
        name=name,
        ordinal_position=ordinal,
        data_type=type_text,
        not_null=True if re.search(r"\bNOT\s+NULL\b", upper) else None,
        default_value=default,
    )


def _first_identifier(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    if stripped[0] == '"':
        end = stripped.find('"', 1)
        while end >= 0 and end + 1 < len(stripped) and stripped[end + 1] == '"':
            end = stripped.find('"', end + 2)
        if end > 0:
            return stripped[1:end].replace('""', '"'), stripped[end + 1 :].strip()
    match = re.match(r"([A-Za-z_][A-Za-z0-9_$]*)\s+(.*)$", stripped, re.S)
    if not match:
        return "", ""
    return match.group(1), match.group(2).strip()


def _type_prefix(rest: str) -> str:
    tokens = _split_ws_top_level(rest.strip())
    parts = []
    for token in tokens:
        upper = token.upper()
        if upper in {
            "NOT",
            "NULL",
            "DEFAULT",
            "PRIMARY",
            "UNIQUE",
            "REFERENCES",
            "COMMENT",
            "COLLATE",
            "CONSTRAINT",
            "CHECK",
        }:
            break
        parts.append(token)
    return " ".join(parts).strip()


def _split_ws_top_level(text: str) -> list[str]:
    parts = []
    start = 0
    depth = 0
    in_quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch.isspace() and depth == 0:
                if start < i:
                    parts.append(text[start:i])
                start = i + 1
        i += 1
    if start < len(text):
        parts.append(text[start:])
    return [part for part in parts if part]


def _paren_body(sql: str) -> str:
    start = sql.find("(")
    if start < 0:
        return ""
    depth = 0
    in_quote = False
    i = start
    while i < len(sql):
        ch = sql[i]
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return sql[start + 1 : i]
        i += 1
    return ""


def _split_top_level_csv(text: str) -> list[str]:
    parts = []
    start = 0
    depth = 0
    in_quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append(text[start:i])
                start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _identifier_list(text: str) -> list[str]:
    if not text:
        return []
    return [_unquote(item.strip()) for item in _split_top_level_csv(text) if item.strip()]


def _samples_by_column(sample_rows: list) -> dict[str, list[str]]:
    samples: dict[str, list[str]] = {}
    for row in sample_rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if value is None:
                continue
            rendered = _sample_preview(value)
            values = samples.setdefault(str(key), [])
            if len(values) >= _MAX_SAMPLE_VALUES:
                continue
            if rendered not in values:
                values.append(rendered)
    return samples


def _sample_preview(value: Any) -> str:
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        rendered = str(value)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    if len(rendered) > _MAX_SAMPLE_CHARS:
        return rendered[: _MAX_SAMPLE_CHARS - 3].rstrip() + "..."
    return rendered


def _merge_column(rel: RelationMeta, incoming: ColumnMeta) -> None:
    key = incoming.name.upper()
    existing = rel.columns.get(key)
    if existing is None:
        rel.columns[key] = incoming
        return
    existing.ordinal_position = min(existing.ordinal_position or incoming.ordinal_position, incoming.ordinal_position)
    existing.data_type = incoming.data_type or existing.data_type
    if incoming.not_null is not None:
        existing.not_null = incoming.not_null
    existing.default_value = incoming.default_value or existing.default_value
    existing.official_column_description = (
        incoming.official_column_description or existing.official_column_description
    )
    for value in incoming.sample:
        if value not in existing.sample:
            existing.sample.append(value)
        if len(existing.sample) >= _MAX_SAMPLE_VALUES:
            break


def _split_relation_name(raw: str, db_id: str, default_schema: str) -> tuple[str, str]:
    parts = [_unquote(part) for part in str(raw).split(".") if part]
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1]
    return default_schema, parts[0] if parts else raw


def _unquote(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('""', '"')
    return text.strip('"`[]')


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_type_label(sql_type: str) -> str:
    value = (sql_type or "").upper()
    if any(token in value for token in ("INT", "NUMBER", "NUMERIC")):
        return "INT"
    if any(token in value for token in ("REAL", "DOUBLE", "DECIMAL")):
        return "REAL"
    if "FLOAT" in value:
        return "FLOAT"
    if any(token in value for token in ("TEXT", "CHAR", "VARCHAR", "STRING")):
        return "TEXT"
    if any(token in value for token in ("BINARY", "VARBINARY")):
        return "BLOB"
    if any(token in value for token in ("VARIANT", "OBJECT", "ARRAY", "JSON")):
        return "JSON"
    if "BOOL" in value:
        return "BOOL"
    if any(token in value for token in ("DATE", "TIME", "TIMESTAMP")):
        return "DATETIME"
    return "TEXT"


def _delete_existing_schema(workspace: Workspace) -> None:
    _write_cypher(
        workspace,
        """
        MATCH (n)
        WHERE n.project = $project
          AND (n:db OR n:schema OR n:table OR n:view OR n:col OR n:fk)
        DETACH DELETE n
        """,
    )


def _write_schema(
    workspace: Workspace,
    db_id: str,
    schemas: set[str],
    relations: dict[tuple[str, str], RelationMeta],
    foreign_keys: list[ForeignKeyMeta],
) -> None:
    for project in workspace.active_projects:
        db_ref = db_id
        db_connect = f"<pontis:{project}:snowflake:connect:{db_id}>"
        table_count = sum(1 for rel in relations.values() if rel.kind == "table")
        view_count = sum(1 for rel in relations.values() if rel.kind == "view")

        db_rows = [{
            "_ref": db_ref,
            "_db_connect": db_connect,
            "name": db_id,
            "table_count": table_count,
            "view_count": view_count,
            "_source_anchor": True,
            "labels": ["db", "snowflake"],
        }]
        schema_rows = [
            {
                "_ref": f"{db_ref}--{schema}",
                "_db_ref": db_ref,
                "_db_connect": db_connect,
                "name": schema,
                "table_count": sum(1 for (s, _), rel in relations.items() if s == schema and rel.kind == "table"),
                "view_count": sum(1 for (s, _), rel in relations.items() if s == schema and rel.kind == "view"),
                "labels": ["schema"],
            }
            for schema in sorted(schemas)
        ]
        relation_rows: list[dict] = []
        column_rows_by_label: dict[str, list[dict]] = {label: [] for label in _TYPE_LABELS}
        edges: list[dict] = [{"a": db_ref, "b": row["_ref"]} for row in schema_rows]

        for (schema, _), rel in sorted(relations.items(), key=lambda item: (item[0][0], item[1].name)):
            schema_ref = f"{db_ref}--{schema}"
            relation_ref = f"{schema_ref}--{rel.name}"
            label = "view" if rel.kind == "view" else "table"
            relation_props = {
                "_ref": relation_ref,
                "_db_ref": db_ref,
                "_schema_ref": schema_ref,
                "_db_connect": db_connect,
                "name": rel.name,
                "ddl": rel.ddl,
                "column_count": len(rel.columns),
                "primary_key": rel.primary_key,
                "labels": [label],
            }
            if rel.official_description:
                relation_props[f"official_{label}_description"] = rel.official_description
            relation_rows.append(relation_props)
            edges.append({"a": schema_ref, "b": relation_ref})

            ordered_columns = sorted(rel.columns.values(), key=lambda col: (col.ordinal_position, col.name))
            for col in ordered_columns:
                type_label = _normalize_type_label(col.data_type)
                col_ref = f"{relation_ref}--{col.name}"
                semantic_profile = classify_semantic_domain(
                    col.name,
                    col.data_type,
                    official_description=col.official_column_description,
                    sample_values=col.sample,
                )
                props = {
                    "_ref": col_ref,
                    "_db_ref": db_ref,
                    "_schema_ref": schema_ref,
                    "_table_ref": relation_ref,
                    "_db_connect": db_connect,
                    "name": col.name,
                    "ordinal_position": col.ordinal_position,
                    "data_type": col.data_type,
                    "not_null": col.not_null,
                    "default_value": col.default_value,
                    "official_column_description": col.official_column_description,
                    "sample": col.sample,
                    "semantic_domain_profile": semantic_profile,
                    "domain_role": semantic_profile["primary_role"],
                    "join_likelihood": semantic_profile["join_likelihood"],
                    "domain_classification_confidence": semantic_profile["classification_confidence"],
                    "semantic_domains": semantic_profile["semantic_domains"],
                    "representation_domains": semantic_profile["representation_domains"],
                    "domain_blocking_keys": semantic_profile["blocking_keys"],
                    "labels": ["col", type_label],
                }
                column_rows_by_label.setdefault(type_label, []).append(props)
                edges.append({"a": relation_ref, "b": col_ref})

        fk_rows: list[dict] = []
        for idx, fk in enumerate(foreign_keys, start=1):
            source_table_ref = _resolve_relation_ref(db_ref, relations, fk.source_table)
            target_table_ref = _resolve_relation_ref(db_ref, relations, fk.target_table)
            if not source_table_ref:
                continue
            ref = f"{source_table_ref}--fk--{idx}"
            fk_rows.append({
                "_ref": ref,
                "_db_ref": db_ref,
                "_db_connect": db_connect,
                "name": _fk_name(fk),
                "source_columns": fk.source_columns,
                "target_table": fk.target_table,
                "target_columns": fk.target_columns,
                "constraint_name": fk.constraint_name,
                "brief": _fk_name(fk),
                "labels": ["fk"],
            })
            edges.append({"a": db_ref, "b": ref})
            edges.append({"a": source_table_ref, "b": ref})
            if target_table_ref:
                edges.append({"a": target_table_ref, "b": ref})

        _upsert_nodes(workspace, project, db_rows, ["db", "snowflake"])
        _upsert_nodes(workspace, project, schema_rows, ["schema"])
        _upsert_nodes(workspace, project, [row for row in relation_rows if "table" in row["labels"]], ["table"])
        _upsert_nodes(workspace, project, [row for row in relation_rows if "view" in row["labels"]], ["view"])
        for label, rows in column_rows_by_label.items():
            _upsert_nodes(workspace, project, rows, ["col", label])
        _upsert_nodes(workspace, project, fk_rows, ["fk"])
        _upsert_edges(workspace, project, edges)


def _resolve_relation_ref(
    db_ref: str,
    relations: dict[tuple[str, str], RelationMeta],
    relation_name: str,
) -> str:
    wanted = _unquote(str(relation_name or "").split(".")[-1]).upper()
    matches = [
        f"{db_ref}--{schema}--{rel.name}"
        for (schema, key), rel in relations.items()
        if key == wanted or rel.name.upper() == wanted
    ]
    return matches[0] if len(matches) == 1 else ""


def _fk_name(fk: ForeignKeyMeta) -> str:
    return f"{fk.source_table}({', '.join(fk.source_columns)})->{fk.target_table}({', '.join(fk.target_columns)})"


def _upsert_nodes(workspace: Workspace, project: str, rows: list[dict], labels: list[str]) -> None:
    if not rows:
        return
    safe_labels = [label for label in labels if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", label)]
    label_clause = "".join(f":{label}" for label in safe_labels)
    for chunk in _chunks(rows, _UPSERT_BATCH_SIZE):
        payload = [
            {
                "_ref": row["_ref"],
                "labels": row.get("labels", labels),
                "props": neo4j_props({k: v for k, v in row.items() if k != "labels" and v not in (None, "", [])}),
            }
            for row in chunk
        ]
        _write_cypher(
            workspace,
            f"""
            UNWIND $rows AS row
            MERGE (n {{_ref: row._ref}})
            ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)
            ON MATCH SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8))
            SET n += row.props
            SET n.project = $project
            SET n.labels = reduce(acc = [], label IN coalesce(n.labels, []) + row.labels |
                CASE WHEN label IN acc THEN acc ELSE acc + label END)
            SET n{label_clause}
            """,
            params={"rows": payload},
            project=project,
        )


def _upsert_edges(workspace: Workspace, project: str, edges: list[dict]) -> None:
    dedup = {(edge["a"], edge["b"]) for edge in edges if edge.get("a") and edge.get("b")}
    for chunk in _chunks([{"a": a, "b": b} for a, b in sorted(dedup)], 2000):
        _write_cypher(
            workspace,
            """
            UNWIND $edges AS edge
            MATCH (a {_ref: edge.a})
            MATCH (b {_ref: edge.b})
            MERGE (a)-[:RELATED_TO]->(b)
            """,
            params={"edges": chunk},
            project=project,
        )


def _chunks(items: list, size: int):
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _write_cypher(
    workspace: Workspace,
    query: str,
    params: dict | None = None,
    *,
    project: str | None = None,
) -> list:
    params = dict(params or {})
    rows: list = []
    projects = [project] if project else workspace.active_projects
    for project_name in projects:
        store = workspace._get_store(project_name)
        if store is None:
            continue
        scoped_params = dict(params)
        scoped_params["project"] = project_name
        with store.execution_lock:
            rows.extend(store.execute_cypher(query, params=scoped_params))
    return rows
