"""Meta tool — 双模式元数据查看。

模式1：meta(ref) → 自身 meta + related 分组
模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居
"""
from typing import List, Optional, Union

from tool.utils.formatters import format_labels, get_info, format_meta_output
from tool.config import resolve_meta_config

_ADJACENCY_KEYS = {"fk", "rel", "disambig", "col", "overlap", "table", "view"}


def _get_project_name(workspace) -> str:
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def _resolve_adjacency(meta: dict, workspace, labels: List[str]) -> dict:
    """将邻接 ref 列表解析为 'entity_name | info' 格式的多行字符串。"""
    resolved = dict(meta)
    for key in _ADJACENCY_KEYS:
        refs = meta.get(key)
        if not isinstance(refs, list) or not refs:
            continue

        lines = []
        for ref in refs:
            adj_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": ref})
            adj_meta = adj_meta_rows[0].get("n") if adj_meta_rows else None
            adj_labels = adj_meta.get("labels", []) if adj_meta else []
            info = get_info(adj_labels, adj_meta or {})
            label_str = format_labels(adj_labels)
            lines.append(f"  {ref}\t{label_str}\t{info}")

        if lines:
            resolved[key] = "\n".join(lines)

    return resolved


def _find_neighbors_by_label(workspace, ref: str, neighbor_label: str) -> List[tuple]:
    """找所有标签匹配 neighbor_label 的邻居。"""
    neighbor_rows = workspace.cypher("MATCH (n {name: $name})--(m) RETURN m", params={"name": ref})
    neighbors = [r["m"]["name"] for r in neighbor_rows if r.get("m")]
    results = []
    for n in neighbors:
        n_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": n})
        n_meta = n_meta_rows[0].get("n") if n_meta_rows else None
        if not n_meta:
            continue
        n_labels = n_meta.get("labels", [])
        if _label_matches(n_labels, neighbor_label):
            results.append((n, n_labels, n_meta))
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
    project_name = _get_project_name(workspace)

    if not workspace.pontis_exists:
        return f"Error: .pontis directory not found in {workspace.project_path}"

    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": ref})
    meta = meta_rows[0].get("n") if meta_rows else None

    if meta is None:
        return f"No metadata found for '{ref}'"

    if not meta:
        return f"Empty metadata for '{ref}'"

    labels = meta.get("labels", [])

    # 模式2：邻居标签过滤
    if neighbor_label:
        neighbors = _find_neighbors_by_label(workspace, ref, neighbor_label)
        return _format_neighbor_list(project_name, neighbors)

    # 模式1：自身 meta + related 分组
    # 分离邻接信息和普通属性
    adjacency = {}
    plain_meta = dict(meta)
    for key in _ADJACENCY_KEYS:
        refs = plain_meta.pop(key, None)
        if isinstance(refs, list) and refs:
            lines = []
            for r in refs:
                adj_meta_rows = workspace.cypher(
                    "MATCH (n {name: $name}) RETURN n", params={"name": r}
                )
                adj_meta = adj_meta_rows[0].get("n") if adj_meta_rows else None
                adj_labels = adj_meta.get("labels", []) if adj_meta else []
                info = get_info(adj_labels, adj_meta or {})
                label_str = format_labels(adj_labels)
                lines.append(f"  {r}\t{label_str}\t{info}")
            if lines:
                adjacency[key] = lines

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
    header_line = f"{ref}\t{format_labels(labels)}\nproject: {project_name}"
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
