"""
Glob tool - Node retrieval via Store graph traversal.

Uses store.find_nodes() for pattern matching with native :: edge traversal.
Supports bidirectional traversal and per-hop dedup.

Returns format: [name] | [Info]
"""
import os
from typing import Optional

from tool_use.utils.formatters import get_type_config, get_file_type_from_name, format_info_from_meta
from tool_use.config import TOOL_PAGINATION


def _format_node_info(store, ref: str, meta: dict) -> str:
    """Format brief info for a node (file or entity)."""
    is_entity = "::" in ref
    name = ref.split("::", 1)[1] if is_entity else ref

    node_type = meta.get('type', '')
    file_type = get_file_type_from_name(name, node_type)
    config = get_type_config(file_type)
    info = format_info_from_meta(meta, config)

    brief = meta.get("brief", "")
    if brief:
        return f"{info}, {brief}" if info != "-" else brief
    return info


def _common_ref_prefix(refs: list) -> str:
    """找所有 ref 的最长公共前缀，停在 :: 边界上。"""
    if not refs:
        return ""
    prefix = refs[0]
    for s in refs[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            return ""
    # 只在 :: 边界截断，确保缩写后剩余部分仍有意义
    if "::" not in prefix:
        return ""  # 没到 entity 层级，不值得缩写
    # 保留到最后一个 :: 后面（如 "california_schools.db::"）
    last_sep = prefix.rfind("::")
    return prefix[:last_sep + 2]


def glob_command(
    store,
    path_pattern: str,
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = ""
) -> str:
    """
    Search nodes via Store graph traversal using path::entity patterns.

    Delegates to store.find_nodes() which handles:
    - Single segment: match file nodes and entity nodes
    - Multi-segment (::): bidirectional edge traversal with dedup

    Args:
        store: Store instance
        path_pattern: Glob pattern with optional :: segments
        offset: Starting index (0-based)
        limit: Max results per page
        current_cwd: Current working directory (unused, kept for compat)

    Returns:
        Formatted results: [ref] | [Info]
    """
    page_conf = TOOL_PAGINATION["glob"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    if not store.pontis_exists:
        return "No .pontis directory found. Run extractor first."

    refs = store.find_nodes(path_pattern)

    if not refs:
        return "No objects found"

    # Build all result lines
    all_refs = []
    all_infos = []
    for ref in refs:
        meta = store.get_meta(ref)
        if meta is None:
            continue
        info = _format_node_info(store, ref, meta)
        all_refs.append(ref)
        all_infos.append(info)

    if not all_refs:
        return "No objects found"

    total = len(all_refs)

    # 检测 ref 公共前缀（如 "california_schools.db::"），缩写以节省 token
    prefix = _common_ref_prefix(all_refs)
    if prefix:
        header = f"[{prefix}]\n"
    else:
        header = ""
        prefix = ""

    page_refs = all_refs[offset:offset + limit]
    page_infos = all_infos[offset:offset + limit]

    if not page_refs:
        return f"No results at offset {offset}. Total results: {total}"

    lines = []
    for ref, info in zip(page_refs, page_infos):
        display = ref[len(prefix):] if prefix else ref
        lines.append(f"{display} | {info}")
    output = header + "\n".join(lines)

    end = offset + len(page_refs)
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
