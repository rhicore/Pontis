"""Meta tool — 双模式元数据查看。

模式1：meta(ref) → 自身 meta + related 分组
模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居
"""
from typing import List, Optional, Union

from tool.utils.formatters import format_labels, get_info, format_meta_output
from tool.config import resolve_meta_config
from tool.utils.entity_refs import entity_display_ref
from tool.utils.resolve import resolve_entity

_ADJACENCY_KEYS = {"fk", "rel", "disambig", "col", "overlap", "table", "view"}
_DB_FILE_SUFFIXES = (".sqlite", ".db", ".sqlite3", ".duckdb")


def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project, local_ref


def _find_neighbors_by_label(store, ent_id: str, neighbor_label: str) -> List[tuple]:
    """找所有标签匹配 neighbor_label 的邻居。"""
    results = []
    for neighbor_id in store._adjacent.get(ent_id, set()):
        n_meta = store._get_meta(neighbor_id)
        if not n_meta:
            continue
        n_labels = n_meta.get("_labels", n_meta.get("labels", []))
        if _label_matches(n_labels, neighbor_label):
            results.append((entity_display_ref(store, neighbor_id), n_labels, n_meta))
    return results


def _label_matches(entity_labels: List[str], query: str) -> bool:
    """扁平标签匹配。"""
    from storage.labels import label_matches
    return label_matches(entity_labels, query)


def _format_neighbor_list(project_name: str, neighbors: List[tuple]) -> str:
    """格式化邻居列表。"""
    if not neighbors:
        return "No matching neighbors found"
    lines = []
    for name, labels, meta in neighbors:
        info = get_info(labels, meta)
        label_str = format_labels(labels)
        lines.append(f"{project_name}::\t{name}\t{label_str}\t{info}")
    return "\n".join(lines)


def meta_command(
    workspace,
    ref: str,
    all: bool = False,
    property: Optional[Union[str, List[str]]] = None,
    neighbor_label: Optional[str] = None,
    current_cwd: str = ""
) -> str:
    """查看节点元数据。

    模式1：meta(ref) → 自身 meta + related 分组
    模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居

    Args:
        workspace: Workspace 实例
        ref: 实体名
        all: 显示全部字段
        property: 指定字段
        neighbor_label: 邻居标签过滤
    """
    explicit_project, _ = _split_project_ref(ref)
    if explicit_project and explicit_project.lower().endswith(_DB_FILE_SUFFIXES):
        return (
            "Error: 你把数据库文件名误写成了项目名前缀。"
            f"请把 `{explicit_project}::...` 改成 `{explicit_project}/...`，"
            "只有真正的项目名才使用 `project::ref` 语法。"
        )
    project_name = explicit_project or _get_project_name(workspace)
    store = workspace._get_store(explicit_project)

    if not store or not store.pontis_exists:
        return f"Error: .pontis directory not found in {workspace.project_path}"

    eid, err = resolve_entity(workspace, ref)
    if err:
        return f"Error: {err}"
    if not store or not eid:
        return f"No metadata found for '{ref}'"

    meta = store._get_meta(eid)
    if meta is None:
        return f"No metadata found for '{ref}'"
    if not meta:
        return f"Empty metadata for '{ref}'"

    labels = meta.get("_labels", meta.get("labels", []))
    display_ref = entity_display_ref(store, eid)

    # 模式2：邻居标签过滤
    if neighbor_label:
        neighbors = _find_neighbors_by_label(store, eid, neighbor_label)
        return _format_neighbor_list(project_name, neighbors)

    # 模式1：自身 meta + related 分组
    # 分离邻接信息和普通属性
    adjacency = {}
    plain_meta = dict(meta)
    for key in _ADJACENCY_KEYS:
        plain_meta.pop(key, None)

    for adj_id in store._adjacent.get(eid, set()):
        adj_meta = store._get_meta(adj_id)
        if not adj_meta:
            continue
        adj_labels = adj_meta.get("_labels", adj_meta.get("labels", []))
        if not adj_labels:
            continue
        group_key = adj_labels[0]
        if group_key not in _ADJACENCY_KEYS:
            continue
        info = get_info(adj_labels, adj_meta or {})
        label_str = format_labels(adj_labels)
        disp = entity_display_ref(store, adj_id)
        adjacency.setdefault(group_key, []).append(f"  {disp}\t{label_str}\t{info}")

    # Normalize property to list
    props = None
    if property:
        if isinstance(property, str):
            props = [property]
        else:
            props = list(property)

    if props:
        from tool.utils.formatters import _format_meta_value
        lines = []
        missing = []
        for p in props:
            # 优先从普通属性查找，再从邻接查找
            value = plain_meta.get(p)
            if value is None and p in adjacency:
                value = "\n".join(adjacency[p])
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(set(list(plain_meta.keys()) + list(adjacency.keys())))
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return "\n".join(lines)

    # Header
    header_line = f"{display_ref}\t{format_labels(labels)}\nproject: {project_name}"
    config = resolve_meta_config(labels)

    # Format plain meta output
    result = format_meta_output(plain_meta, config, show_all=all, specific_key=None)

    if not result and not all:
        lines = [header_line, ""]
        for key, value in sorted(plain_meta.items()):
            lines.append(f"{key}: {value}")
        result = "\n".join(lines)
    else:
        result = header_line + "\n\n" + result

    # 追加邻接节点（与 glob 格式一致：name \t :labels \t info）
    if adjacency:
        # 按配置过滤要显示的邻接类型
        visible_adj = {k: v for k, v in adjacency.items()
                       if not config.adjacency_keys or k in config.adjacency_keys}
        if visible_adj:
            summary_parts = [f"{k}: {len(v)}" for k, v in sorted(visible_adj.items())]
            result += f"\n\nRelated ({', '.join(summary_parts)})\n"

            for key in sorted(visible_adj.keys()):
                result += f"\n{key}:\n" + "\n".join(visible_adj[key]) + "\n"

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.meta.tool <project_name> <path> [--all] [+property]")
        sys.exit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    _path = sys.argv[2]
    _all = '--all' in sys.argv or '-a' in sys.argv
    _prop = None
    for arg in sys.argv[3:]:
        if arg.startswith('+'):
            _prop = arg[1:]

    print(meta_command(ws, _path, _all, _prop))
