"""Delete tool — 删除节点，支持级联删除。"""

from tool.utils.resolve import resolve_entity
from tool.config import resolve_rank


def _get_entity_rank(ref: str, workspace) -> int:
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": ref})
    meta = meta_rows[0].get("n") if meta_rows else None
    labels = meta.get("labels", []) if meta else []
    return resolve_rank(labels)


def _is_more_derived(ref_a: str, ref_b: str, workspace) -> bool:
    return _get_entity_rank(ref_a, workspace) > _get_entity_rank(ref_b, workspace)


def delete_command(workspace, ref: str) -> str:
    """通过 Cypher DELETE 删除节点。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - glob 模式 → 必须匹配唯一实体
    """
    if not workspace.pontis_exists:
        return f"错误: 未找到 .pontis 目录 ({workspace.project_path})"

    # 解析实体引用
    eid, err = resolve_entity(workspace, ref)
    if err:
        return f"Error: {err}"

    project = ref.split("::", 1)[0] if "::" in ref else None
    store = workspace._get_store(project)
    if store is None:
        return "错误: 当前没有可用的项目 store"

    name = store._id_index.get(eid, {}).get("name", "")
    if not name:
        return f"节点不存在: {ref}"

    # 级联查找派生实体
    # 只对已知层级关系级联：file→table, table→col, col→fk/rel/overlap
    _CASCADE_FROM = {"file", "db", "csv", "table", "view", "col"}
    to_delete = [eid]
    main_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": name})
    main_meta = main_meta_rows[0].get("n") if main_meta_rows else None
    main_labels = set(main_meta.get("labels", [])) if main_meta else set()
    if main_labels & _CASCADE_FROM:
        neighbor_rows = workspace.cypher('MATCH (n {name: $name})--(m) RETURN m', params={"name": name})
        neighbors = [r["m"]["name"] for r in neighbor_rows if r.get("m")]
        for neighbor_ref in neighbors:
            if _is_more_derived(neighbor_ref, name, workspace):
                if workspace.cypher('MATCH (n {name: $name}) RETURN n', params={"name": neighbor_ref}):
                    nid = store._resolve_to_id(neighbor_ref)
                    if nid:
                        to_delete.append(nid)

    # 逐个删除
    deleted = []
    for tid in to_delete:
        deleted_name = store._id_index.get(tid, {}).get("name", "")
        removed = store._delete_node(tid)
        if removed:
            deleted.append(deleted_name or removed)

    if not deleted:
        return f"删除失败: {ref}"

    lines = [f"已删除 {len(deleted)} 个节点:"]
    for d in deleted:
        lines.append(f"  - {d}")
    return "\n".join(lines)
