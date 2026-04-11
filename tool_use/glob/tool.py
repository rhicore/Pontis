"""
Glob tool - Physical file and entity retrieval.

Based on path::entity pattern syntax:
1. First glob physical files matching the file pattern
2. For each matched file, optionally glob logical entities matching entity pattern

Returns format: [name] | [Info]
Truncation at 100 files: "(Results are truncated. Consider using a more specific path or pattern.)"
No match: "No objects found"
"""
import os
from typing import Optional

from tool_use.utils.path_parser import parse_path_pattern
from tool_use.utils.formatters import get_type_config, get_file_type_from_name, format_info_from_meta
from tool_use.utils.config import TOOL_PAGINATION


def _format_file_info(store, file_rel_path: str) -> str:
    """Get brief info for a physical file."""
    if store.is_directory(file_rel_path):
        entries = store.list_dir(file_rel_path)
        file_count = sum(1 for e in entries if os.path.isfile(
            os.path.join(store.project_path, file_rel_path, e)))
        subdir_count = len(entries) - file_count
        config = get_type_config("directory")
        meta = {"child_count": len(entries), "file_count": file_count, "subdir_count": subdir_count}
        return format_info_from_meta(meta, config)

    meta = store.get_meta(file_rel_path) or {}

    ext = os.path.splitext(file_rel_path)[1].lower()
    config = get_type_config(ext)
    info = format_info_from_meta(meta, config)

    brief = meta.get("brief", "")
    if brief:
        return f"{info}, {brief}"
    return info


def _format_entity_info(store, file_rel_path: str, entity_rel: str) -> str:
    """Get brief info for an entity."""
    meta = store.get_meta(file_rel_path, entity_rel) or {}
    name = os.path.basename(entity_rel)

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
    Search physical files and their logical entities using path::entity patterns.

    Args:
        store: ProjectStore instance
        path_pattern: Glob pattern, optionally with ::entity suffix
        offset: Starting index (0-based)
        limit: Max results per page
        current_cwd: Current working directory

    Returns:
        Formatted results: [name] | [Info]
    """
    parsed = parse_path_pattern(path_pattern)
    page_conf = TOOL_PAGINATION["glob"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    # Step 1: Glob physical files
    file_matches = store.glob_physical_files(parsed.file_pattern, current_cwd)

    if not file_matches:
        return "No objects found"

    # Build all result lines first
    all_results = []
    for file_rel in file_matches:
        if parsed.has_entity and store.pontis_exists:
            entities = store.glob_entities(file_rel, parsed.entity_pattern)
            if entities:
                for entity_rel in entities:
                    display = f"{file_rel}::{entity_rel}"
                    info = _format_entity_info(store, file_rel, entity_rel)
                    all_results.append(f"{display} | {info}")
            else:
                info = _format_file_info(store, file_rel)
                all_results.append(f"{file_rel} | {info}")
        else:
            info = _format_file_info(store, file_rel)
            all_results.append(f"{file_rel} | {info}")

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
        print("Usage: python -m tool_use.glob.tool <project_path> <path_pattern> [cwd]")
        sys.exit(1)

    from storage import ProjectStore
    _store = ProjectStore(sys.argv[1])
    _pattern = sys.argv[2]
    _cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(glob_command(_store, _pattern, _cwd))
