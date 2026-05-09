"""Tool-facing entity ref helpers.

在不改变 storage/cypher 返回模型的前提下，为 tool 层提供：
- agent 可见的稳定路径式 ref（如 financial.sqlite/account/account_id）
- 路径式 ref 到内部 scoped ref（如 financial.sqlite--account--account_id）的转换
"""

from __future__ import annotations


def path_ref_to_internal(ref: str) -> str:
    if "/" not in ref:
        return ref
    return ref.replace("/", "--")


def dotted_ref_to_path(ref: str) -> str:
    if "/" in ref or "." not in ref:
        return ref
    head, tail = ref.rsplit(".", 1)
    if not head or not tail:
        return ref
    return f"{head}/{tail}"


def entity_display_ref(store, ent_or_ref: str) -> str:
    ent_id = ent_or_ref if ent_or_ref.startswith("ent_") else store._resolve_to_id(path_ref_to_internal(ent_or_ref))
    if not ent_id:
        return ent_or_ref

    props = store._id_index.get(ent_id, {})
    name = props.get("name", ent_or_ref)
    labels = props.get("_labels", [])

    if "col" in labels:
        parent_id = _first_neighbor_with_label(store, ent_id, {"table", "view"})
        if parent_id:
            parent_name = store._id_index.get(parent_id, {}).get("name", "")
            file_id = _first_neighbor_with_label(store, parent_id, {"file"})
            if file_id:
                file_name = store._id_index.get(file_id, {}).get("name", "")
                return f"{file_name}/{parent_name}/{name}"
            return f"{parent_name}/{name}"

    if "table" in labels or "view" in labels:
        file_id = _first_neighbor_with_label(store, ent_id, {"file"})
        if file_id:
            file_name = store._id_index.get(file_id, {}).get("name", "")
            return f"{file_name}/{name}"

    return name


def row_display_ref(store, row: dict, main_var: str, main_info: dict) -> str:
    labels = main_info.get("labels", [])
    name = main_info.get("name", "?")

    if "col" in labels:
        table_name = None
        file_name = None
        for var, info in row.items():
            if not isinstance(info, dict):
                continue
            var_labels = info.get("labels", [])
            if table_name is None and ("table" in var_labels or "view" in var_labels):
                table_name = info.get("name")
            if file_name is None and "file" in var_labels:
                file_name = info.get("name")
        if table_name and file_name:
            return f"{file_name}/{table_name}/{name}"
        if table_name:
            return f"{table_name}/{name}"

    if "table" in labels or "view" in labels:
        file_name = None
        for var, info in row.items():
            if var == main_var or not isinstance(info, dict):
                continue
            if "file" in info.get("labels", []):
                file_name = info.get("name")
                break
        if file_name:
            return f"{file_name}/{name}"

    return entity_display_ref(store, name)


def disambiguated_display_refs(store, ref: str) -> list[str]:
    internal = path_ref_to_internal(ref)
    ids = store._name_to_ids(internal)
    if not ids and internal != ref:
        return []
    if not ids:
        ids = store._name_to_ids(ref)
    return [entity_display_ref(store, ent_id) for ent_id in ids]


def _first_neighbor_with_label(store, ent_id: str, labels: set[str]) -> str | None:
    for adj_id in store._adjacent.get(ent_id, set()):
        adj_labels = set(store._id_index.get(adj_id, {}).get("_labels", []))
        if adj_labels & labels:
            return adj_id
    return None
