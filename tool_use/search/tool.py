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
import sys
import fnmatch
from typing import Optional, List

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_use.utils.path_parser import parse_path_pattern
from tool_use.utils.formatters import get_type_config, format_info_from_meta, get_file_type_from_name

MAX_RESULTS = 100


def _keyword_search_meta(pontis_root: str, query: str, path_pattern: str = "") -> List[tuple]:
    """
    Fallback keyword search in _meta.yml files.

    Searches short_summary, name, and type fields for keyword matches.
    """
    results = []
    query_lower = query.lower()
    query_words = set(query_lower.split())

    parsed = parse_path_pattern(path_pattern) if path_pattern else None

    for root, dirs, files in os.walk(pontis_root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for fname in files:
            if fname != '_meta.yml':
                continue

            meta_path = os.path.join(root, fname)
            rel_dir = os.path.relpath(root, pontis_root)

            try:
                with open(meta_path, 'r') as f:
                    meta = yaml.safe_load(f) or {}
            except Exception:
                continue

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
                brief = meta.get(config.brief_field, '')

                display = rel_dir
                combined_info = f"{info}, {brief}" if brief and info != "-" else (brief or info)
                results.append((score, display, combined_info))
    return results


def search_command(
    project_path: str,
    path_pattern: str,
    query: str,
    structure_enhance: bool = True,
    BM25: bool = True,
    current_cwd: str = ""
) -> str:
    """
    Semantic search across files and entities.

    Args:
        project_path: Path to project root
        path_pattern: Glob pattern for narrowing search scope
        query: Natural language search query
        structure_enhance: Whether to use KG structure (not yet implemented)
        BM25: Whether to use BM25 keyword search (not yet implemented)
        current_cwd: Current working directory

    Returns:
        Formatted results: [name] | [Info]
    """
    pontis_root = os.path.join(project_path, ".pontis")

    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    # TODO: Implement KG-enhanced search when KG infrastructure is available
    # TODO: Implement BM25 search when index infrastructure is available

    # Fallback: keyword search in metadata
    results = _keyword_search_meta(pontis_root, query, path_pattern)

    if not results:
        return "No objects found"

    # Sort by score (descending)
    results.sort(key=lambda x: -x[0])

    # Format output
    lines = []
    truncated = len(results) > MAX_RESULTS
    for score, display, info in results[:MAX_RESULTS]:
        lines.append(f"{display} | {info}")

    output = '\n'.join(lines)
    if truncated:
        output += "\n(Results are truncated. Consider using a more specific path or pattern.)"

    return output


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python -m tool_use.search.tool <project_path> <path_pattern> <query>")
        sys.exit(1)

    _project = sys.argv[1]
    _pattern = sys.argv[2]
    _query = sys.argv[3]
    print(search_command(_project, _pattern, _query))
