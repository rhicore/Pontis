"""
Search tool - Semantic search across files and entities.

Uses natural language queries to find relevant objects.
Supports optional KG structure enhancement and BM25 keyword search.

NOTE: This is a placeholder implementation. Full implementation requires:
1. Knowledge Graph (KG) embedding/index infrastructure
2. BM25 index for keyword search
3. Embedding model for semantic similarity

Currently falls back to keyword matching in metadata.
"""
import os
from typing import List, Optional

from tool_use.utils.formatters import get_type_config, format_info_from_meta, get_file_type_from_name
from tool_use.utils.config import TOOL_PAGINATION


def _keyword_search(store, query: str, path_pattern: str = "") -> List[tuple]:
    """
    Fallback keyword search in metadata via store.
    """
    results = []
    query_lower = query.lower()
    query_words = set(query_lower.split())

    for rel_dir, meta in store.walk_all_metas():
        name = meta.get('name', os.path.basename(rel_dir))
        summary = meta.get('short_summary', '') or ''
        long_summary = meta.get('long_summary', '') or ''
        node_type = meta.get('type', '')
        semantic_summary = meta.get('semantic_summary', '') or ''

        # Score based on keyword matches
        searchable = f"{name} {summary} {long_summary} {node_type} {semantic_summary}".lower()
        score = sum(1 for w in query_words if w in searchable)

        if score > 0:
            # Get file type and info
            file_type = get_file_type_from_name(name, node_type)
            config = get_type_config(file_type)
            info = format_info_from_meta(meta, config)
            brief = meta.get("brief", "")

            display = rel_dir
            combined_info = f"{info}, {brief}" if brief and info != "-" else (brief or info)
            results.append((score, display, combined_info))
    return results


def search_command(
    store,
    path_pattern: str,
    query: str,
    offset: int = 0,
    limit: Optional[int] = None,
    structure_enhance: bool = True,
    BM25: bool = True,
    current_cwd: str = ""
) -> str:
    """
    Semantic search across files and entities.

    Args:
        store: ProjectStore instance
        path_pattern: Glob pattern for narrowing search scope
        query: Natural language search query
        offset: Starting index (0-based)
        limit: Max results per page
        structure_enhance: Whether to use KG structure (not yet implemented)
        BM25: Whether to use BM25 keyword search (not yet implemented)
        current_cwd: Current working directory

    Returns:
        Formatted results: [name] | [Info]
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    page_conf = TOOL_PAGINATION["search"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    # Fallback: keyword search in metadata
    results = _keyword_search(store, query, path_pattern)

    if not results:
        return "No objects found"

    # Sort by score (descending)
    results.sort(key=lambda x: -x[0])

    total = len(results)
    page = results[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    lines = [f"{display} | {info}" for score, display, info in page]
    output = '\n'.join(lines)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python -m tool_use.search.tool <project_path> <path_pattern> <query>")
        sys.exit(1)

    from storage import ProjectStore
    _store = ProjectStore(sys.argv[1])
    _pattern = sys.argv[2]
    _query = sys.argv[3]
    print(search_command(_store, _pattern, _query))
