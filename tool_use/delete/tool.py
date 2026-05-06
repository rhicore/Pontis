"""Delete tool executor — 删除节点，支持按标签层级级联删除。"""
from tool_use.config import resolve_rank


def _get_entity_rank(ref: str, store) -> int:
    """从 ref 的 _labels 获取实体依赖层级。"""
    meta = store._get_stored_meta(ref) or {}
    labels = meta.get("_labels", [])
    rank = resolve_rank(labels)
    if rank > 0:
        return rank
    # Fallback: try name suffix
    dot_idx = ref.rfind(".")
    if dot_idx >= 0:
        suffix = ref[dot_idx + 1:]
        from tool_use.config import ENTITY_DEPENDENCY_RANK
        return ENTITY_DEPENDENCY_RANK.get(suffix, -1)
    return -1


def _is_more_derived(ref_a: str, ref_b: str, store) -> bool:
    """判断 ref_a 是否比 ref_b 更是派生实体（rank 更高）。"""
    return _get_entity_rank(ref_a, store) > _get_entity_rank(ref_b, store)


def delete_command(store, ref: str) -> str:
    """删除知识图谱节点，级联删除依赖该节点的派生实体。"""
    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return f"错误: 未找到 .pontis 目录 ({store.project_path})"

    if not store.node_exists(ref):
        return f"节点不存在: {ref}"

    deleted = []
    queue = [ref]

    while queue:
        current = queue.pop(0)

        if not store.node_exists(current):
            continue

        neighbors = store.find_connected(current, pattern="*")

        removed = store.delete_node(current)
        if removed:
            deleted.append(removed)

        for neighbor_ref in neighbors:
            if _is_more_derived(neighbor_ref, current, store):
                if store.node_exists(neighbor_ref):
                    queue.append(neighbor_ref)

    if not deleted:
        return f"删除失败: {ref}"

    lines = [f"已删除 {len(deleted)} 个节点:"]
    for d in deleted:
        lines.append(f"  - {d}")

    return "\n".join(lines)
