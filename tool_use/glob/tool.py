"""
Glob tool - Node retrieval via Store graph traversal.

Uses store.find_nodes() for pattern matching with native :: edge traversal.
Supports bidirectional traversal and per-hop dedup.

Returns format: [name] | [Info]
"""
import os
from typing import Optional

from tool_use.utils.formatters import get_type_config, get_file_type_from_name, format_info_from_meta
from tool_use.utils.config import TOOL_PAGINATION


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
    all_results = []
    for ref in refs:
        meta = store.get_meta(ref)
        if meta is None:
            continue
        info = _format_node_info(store, ref, meta)
        all_results.append(f"{ref} | {info}")

    if not all_results:
        return "No objects found"

    total = len(all_results)
    page = all_results[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    output = "\n".join(page)

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
