"""Extractor 内部实体引用辅助。

这些 helper 只用于抽取阶段的内部定位：
- 不改变实体对外可见的 `name`
- 通过 scoped ref 避免同名列/表在图中被错误复用
"""

from storage.workspace import Workspace


def db_table_ref(db_ref: str, table_name: str) -> str:
    return f"{db_ref}--{table_name}"


def db_view_ref(db_ref: str, view_name: str) -> str:
    return f"{db_ref}--{view_name}"


def db_column_ref(db_ref: str, table_name: str, column_name: str) -> str:
    return f"{db_ref}--{table_name}--{column_name}"


def get_entity_meta(workspace: Workspace, ref: str) -> dict | None:
    store = workspace._get_store()
    return store._get_meta(ref) if store else None


def set_entity_meta(workspace: Workspace, ref: str, props: dict) -> None:
    store = workspace._get_store()
    if store:
        store._set_meta(ref, props)
