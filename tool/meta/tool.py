"""Meta tool — 双模式元数据查看。

模式1：meta(ref) → 自身 meta + related 分组
模式2：meta(ref, neighbor_label) → 只看匹配 neighbor_label 的邻居
"""
import os
from typing import List, Optional, Union

from tool.utils.formatters import format_labels, get_info, format_meta_output
from tool.config import resolve_meta_config

_ADJACENCY_KEYS = {"fk", "rel", "disambig", "col", "overlap", "table", "view"}


def _get_store(obj):
    if hasattr(obj, 'get_store'):
        return obj.get_store()
    return obj


def _get_project_name(obj) -> str:
    if hasattr(obj, 'config'):
        dp = obj.config.default_project()
        if dp:
            return dp
    if hasattr(obj, 'project_path'):
        return os.path.basename(obj.project_path)
    return "local"


def _resolve_adjacency(meta: dict, store, labels: List[str]) -> dict:
    """将邻接 ref 列表解析为 'entity_name | info' 格式的多行字符串。"""
    resolved = dict(meta)
    for key in _ADJACENCY_KEYS:
        refs = meta.get(key)
        if not isinstance(refs, list) or not refs:
            continue

        lines = []
        for ref in refs:
            adj_meta = store.get_meta(ref) or {}
            adj_labels = adj_meta.get("_labels", [])
            info = get_info(adj_labels, adj_meta)
            label_str = format_labels(adj_labels)
            lines.append(f"  {ref}\t{label_str}\t{info}")

        if lines:
            resolved[key] = "\n".join(lines)

    return resolved


def _find_neighbors_by_label(store, ref: str, neighbor_label: str) -> List[tuple]:
    """找所有标签匹配 neighbor_label 的邻居。"""
    neighbors = store.neighbors(ref)
    results = []
    for n in neighbors:
        n_id = store._name_to_id(n)
        if not n_id:
            continue
        n_labels = store._get_labels_by_id(n_id)
        if _label_matches(n_labels, neighbor_label):
            n_meta = store.get_meta(n) or {}
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
    obj,  # Workspace or Store
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
        obj: Workspace 或 Store 实例
        ref: 实体名
        all: 显示全部字段
        property: 指定字段
        neighbor_label: 邻居标签过滤
    """
    store = _get_store(obj)
    project_name = _get_project_name(obj)

    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    meta = store.get_meta(ref)

    if meta is None:
        return f"No metadata found for '{ref}'"

    if not meta:
        return f"Empty metadata for '{ref}'"

    labels = meta.get("_labels", [])

    # 模式2：邻居标签过滤
    if neighbor_label:
        neighbors = _find_neighbors_by_label(store, ref, neighbor_label)
        return _format_neighbor_list(project_name, neighbors)

    # 模式1：自身 meta + related 分组
    # Resolve adjacency lists
    meta = _resolve_adjacency(meta, store, labels)

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
            value = meta.get(p)
            if value is None:
                missing.append(p)
            else:
                lines.append(f"{p}: {_format_meta_value(value, None)}")
        if missing:
            available = sorted(meta.keys())
            lines.append(f"未找到: {', '.join(missing)}. 可用字段: {', '.join(available)}")
        return "\n".join(lines)

    # Header
    header_line = f"{project_name}::\t{ref}\t{format_labels(labels)}"
    config = resolve_meta_config(labels)

    # 邻接 key 不截断
    config.untruncated_keys = config.untruncated_keys | _ADJACENCY_KEYS

    # Format output
    result = format_meta_output(meta, config, show_all=all, specific_key=None)

    if not result and not all:
        lines = [header_line, ""]
        for key, value in sorted(meta.items()):
            lines.append(f"{key}: {value}")
        result = "\n".join(lines)
    else:
        result = header_line + "\n\n" + result

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.meta.tool <project_path> <path> [--all] [+property]")
        sys.exit(1)

    from storage import Store
    _store = Store(sys.argv[1])
    _path = sys.argv[2]
    _all = '--all' in sys.argv or '-a' in sys.argv
    _prop = None
    for arg in sys.argv[3:]:
        if arg.startswith('+'):
            _prop = arg[1:]

    print(meta_command(_store, _path, _all, _prop))
