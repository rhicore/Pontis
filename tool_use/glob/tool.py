"""Glob tool — Cypher URN 查询 + 标签格式化显示。

使用 Finder.find() 进行 URN 解析和图遍历。
显示格式：project://\tname\t:labels\tinfo

支持 workspace 或 store（兼容旧调用方式）。
"""
import os
from typing import Optional, Union

from tool_use.utils.formatters import format_labels, get_info
from tool_use.config import TOOL_PAGINATION


def _get_project_name(obj) -> str:
    """从 workspace 或 store 获取项目名。"""
    if hasattr(obj, 'config'):
        # Workspace
        dp = obj.config.default_project()
        if dp:
            return dp
    # Fallback: 从 store 路径推断
    if hasattr(obj, 'project_path'):
        return os.path.basename(obj.project_path)
    return "local"


def _get_store(obj):
    """从 workspace 获取 store，或直接返回 store。"""
    if hasattr(obj, 'get_store'):
        return obj.get_store()
    return obj


def _do_find(obj, pattern: str):
    """使用 Finder 或 store.find_nodes() 查询。"""
    if hasattr(obj, 'finder'):
        return obj.finder.find(pattern)
    # Fallback: store.find_nodes() 返回 [name, ...]
    store = obj
    names = store.find_nodes(pattern)
    results = []
    for name in names:
        meta = store.get_meta(name) or {}
        labels = meta.get("_labels", [])
        results.append((name, labels))
    return results


def glob_command(
    obj,  # Workspace or Store
    path_pattern: str,
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = ""
) -> str:
    """Cypher URN 查询，返回标签格式化显示。

    Args:
        obj: Workspace 或 Store 实例
        path_pattern: URN pattern，支持 Cypher 风格
        offset: 起始索引
        limit: 每页最大条数
    """
    page_conf = TOOL_PAGINATION["glob"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    store = _get_store(obj)
    if hasattr(store, 'pontis_exists') and not store.pontis_exists:
        return "No .pontis directory found. Run extractor first."

    results = _do_find(obj, path_pattern)

    if not results:
        return "No objects found"

    project_name = _get_project_name(obj)

    # Build all result lines
    all_items = []
    for name, labels in results:
        meta = store.get_meta(name)
        if meta is None:
            continue
        info = get_info(labels, meta)
        label_str = format_labels(labels)
        all_items.append((name, label_str, info))

    if not all_items:
        return "No objects found"

    total = len(all_items)
    page = all_items[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    lines = []
    for name, label_str, info in page:
        lines.append(f"{project_name}://\t{name}\t{label_str}\t{info}")
    output = "\n".join(lines)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.glob.tool <project_path> <path_pattern>")
        sys.exit(1)

    from storage import Store
    _store = Store(sys.argv[1])
    _pattern = sys.argv[2]
    print(glob_command(_store, _pattern))
