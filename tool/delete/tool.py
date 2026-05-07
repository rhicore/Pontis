"""Delete tool — 通过 Cypher DELETE 删除节点，支持级联删除。"""

from tool.utils import execute_cypher
from tool.utils.resolve import resolve_entity
from tool.config import resolve_rank


def _get_entity_rank(ref: str, store) -> int:
    meta = store._get_stored_meta(ref) or {}
    labels = meta.get("_labels", [])
    return resolve_rank(labels)


def _is_more_derived(ref_a: str, ref_b: str, store) -> bool:
    return _get_entity_rank(ref_a, store) > _get_entity_rank(ref_b, store)


def delete_command(obj, ref: str) -> str:
    """通过 Cypher DELETE 删除节点。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - glob 模式 → 必须匹配唯一实体
    """
    store = obj if not hasattr(obj, 'get_store') else obj.get_store()

    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return f"错误: 未找到 .pontis 目录 ({store.project_path})"

    # 解析实体引用
    eid, err = resolve_entity(obj, ref)
    if err:
        return f"Error: {err}"

    # 查找节点名（用于级联）
    match_r = execute_cypher(obj, f'MATCH (n {{id: "{eid}"}}) RETURN n')
    if not match_r:
        return f"节点不存在: {ref}"

    main_info = match_r[0].get("n", {})
    name = main_info.get("name", "")
    if not name:
        return f"节点不存在: {ref}"

    # 级联查找派生实体
    to_delete = [eid]
    neighbors = store.find_connected(name, pattern="*")
    for neighbor_ref in neighbors:
        if _is_more_derived(neighbor_ref, name, store):
            if store.node_exists(neighbor_ref):
                nid = store._name_to_id(neighbor_ref)
                if nid:
                    to_delete.append(nid)

    # 逐个删除
    deleted = []
    for tid in to_delete:
        r = execute_cypher(obj, f'MATCH (n {{id: "{tid}"}}) DELETE n')
        if r:
            for item in r:
                for d in item.get("deleted", []):
                    deleted.append(d["name"])

    if not deleted:
        return f"删除失败: {ref}"

    lines = [f"已删除 {len(deleted)} 个节点:"]
    for d in deleted:
        lines.append(f"  - {d}")
    return "\n".join(lines)
