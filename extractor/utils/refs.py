"""Extractor 内部实体引用辅助。

这些 helper 只用于抽取阶段的内部定位：
- 不改变实体对外可见的 `name`
- 通过 scoped ref 避免同名列/表在图中被错误复用
"""

import json
from typing import Any

from storage.workspace import Workspace


_PRIMITIVE = (str, int, float, bool)


def db_table_ref(db_ref: str, table_name: str) -> str:
    return f"{db_ref}--{table_name}"


def db_view_ref(db_ref: str, view_name: str) -> str:
    return f"{db_ref}--{view_name}"


def db_column_ref(db_ref: str, table_name: str, column_name: str) -> str:
    return f"{db_ref}--{table_name}--{column_name}"


def db_fk_ref(db_ref: str, fk_name: str) -> str:
    return f"{db_ref}--{fk_name}"


def get_entity_meta(workspace: Workspace, ref: str) -> dict | None:
    for prop in ("_ref", "ref", "path", "name"):
        rows = workspace.cypher(
            f"MATCH (n {{{prop}: $ref}}) RETURN n",
            params={"ref": ref},
        )
        if rows:
            return rows[0].get("n")
    return None


def _neo4j_property_value(value: Any):
    if value is None or isinstance(value, _PRIMITIVE):
        return value
    if isinstance(value, list) and all(
        item is None or isinstance(item, _PRIMITIVE)
        for item in value
    ):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def neo4j_props(props: dict) -> dict:
    """Convert extractor metadata to Neo4j property-compatible values."""
    return {key: _neo4j_property_value(value) for key, value in props.items()}


def set_entity_meta(workspace: Workspace, ref: str, props: dict) -> None:
    props = neo4j_props(props)
    rows = workspace.cypher(
        "MATCH (n {_ref: $ref}) SET n += $props RETURN n",
        params={"ref": ref, "props": props},
    )
    if rows:
        return
    rows = workspace.cypher(
        "MATCH (n {ref: $ref}) SET n += $props RETURN n",
        params={"ref": ref, "props": props},
    )
    if rows:
        return
    rows = workspace.cypher(
        "MATCH (n {path: $ref}) SET n += $props RETURN n",
        params={"ref": ref, "props": props},
    )
    if rows:
        return
    rows = workspace.cypher(
        "MATCH (n {name: $ref}) SET n += $props RETURN n",
        params={"ref": ref, "props": props},
    )
    if rows:
        return
    workspace.cypher(
        "CREATE (n {name: $ref}) SET n += $props RETURN n",
        params={"ref": ref, "props": props},
    )
