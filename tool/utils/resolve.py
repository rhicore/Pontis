"""Write 工具共用 — 实体引用解析。

所有 ref 都通过 glob 匹配，必须唯一命中。实体 ID (ent_xxx) 为内部属性，不对外暴露。
"""
from tool.utils.entity_refs import (
    disambiguated_display_refs,
    dotted_ref_to_path,
    path_ref_to_internal,
)


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project, local_ref


def resolve_entity(workspace, ref: str) -> tuple:
    """将实体引用解析为唯一 ent_id。

    匹配逻辑：
      - 精确名称（无通配符、无 URN 语法）→ 直接 store 查找
      - glob/URN 模式 → glob_command 匹配，必须唯一

    Returns:
        (ent_id, error_msg)
        成功时 error_msg 为 None，失败时 ent_id 为 None
    """
    project, local_ref = _split_project_ref(ref)
    store = workspace._get_store(project)
    if store is None:
        if project:
            return (None, f"未知项目: {project}")
        return (None, "当前没有可用的项目 store")

    local_ref = dotted_ref_to_path(local_ref)

    # 精确名称快速路径（无通配符、无遍历段）
    has_wildcards = any(c in local_ref for c in "*?[]")
    is_path_like = "/" in local_ref
    normalized_ref = path_ref_to_internal(local_ref)

    if not has_wildcards and not is_path_like:
        exact_ids = store._name_to_ids(local_ref)
        if len(exact_ids) > 1:
            preferred = _prefer_non_column(store, exact_ids)
            if preferred:
                return (preferred, None)
            refs = disambiguated_display_refs(store, local_ref)
            return (None, f"匹配到 {len(exact_ids)} 个实体，请使用更精确的路径:\n  " + "\n  ".join(refs))
        ent_id = store._resolve_to_id(local_ref)
        if ent_id:
            return (ent_id, None)

    if not has_wildcards and is_path_like:
        ent_id = store._resolve_to_id(normalized_ref)
        if ent_id:
            return (ent_id, None)

    # glob/URN 模式匹配（含 / 路径名、: 标签过滤、* 通配符）
    from tool.glob.tool import glob_command
    output = glob_command(workspace, ref)

    if output.startswith("No objects"):
        return (None, f"未找到匹配的实体: {ref}")

    # 解析结果行，提取 name 并查找 ent_id
    lines = [l for l in output.strip().split("\n") if l.strip() and not l.startswith("(")]
    matched = []
    for line in lines:
        parts = line.split("\t")
        if parts:
            eid = store._resolve_to_id(parts[0])
            if eid:
                matched.append((eid, parts[0]))

    if not matched:
        return (None, f"未找到匹配的实体: {ref}")
    if len(matched) > 1:
        names = [n for _, n in matched]
        return (None, f"匹配到 {len(matched)} 个实体，请使用更精确的模式:\n  " + "\n  ".join(names))

    return (matched[0][0], None)


def _prefer_non_column(store, ent_ids: list[str]) -> str | None:
    non_cols = []
    for ent_id in ent_ids:
        labels = set(store._id_index.get(ent_id, {}).get("_labels", []))
        if "col" not in labels:
            non_cols.append(ent_id)
    if len(non_cols) == 1:
        return non_cols[0]
    return None
