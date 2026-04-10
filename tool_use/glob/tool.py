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
import sys
import glob as pyglob
import fnmatch
from typing import Optional, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_use.utils.path_parser import parse_path_pattern, ParsedPath
from tool_use.utils.formatters import get_type_config, get_file_type_from_name

MAX_RESULTS = 100


def _glob_physical_files(project_path: str, file_pattern: str, cwd: str = "") -> List[str]:
    """
    Glob physical files in the project directory.

    Args:
        project_path: Absolute path to the project root
        file_pattern: Glob pattern for physical files (e.g., "**/*.py", "src/**/*.ts")
        cwd: Current working directory (relative to project root)

    Returns:
        List of relative file paths (relative to project_path)
    """
    search_root = os.path.join(project_path, cwd) if cwd else project_path
    full_pattern = os.path.join(search_root, file_pattern)

    # Use Python's glob for file matching
    matches = pyglob.glob(full_pattern, recursive=True)

    # Convert to relative paths
    results = []
    for m in matches:
        rel = os.path.relpath(m, project_path)
        # Skip .pontis directory
        if '.pontis' in rel.split(os.sep):
            continue
        # Skip .git and other hidden dirs
        parts = rel.split(os.sep)
        if any(p.startswith('.') for p in parts):
            continue
        results.append(rel)

    # Sort by modification time (newest first)
    results.sort(key=lambda p: os.path.getmtime(os.path.join(project_path, p)), reverse=True)
    return results


def _glob_entities_in_file(
    pontis_root: str,
    file_rel_path: str,
    entity_pattern: str
) -> List[Tuple[str, str]]:
    """
    Glob logical entities within a physical file's pontis directory.

    Args:
        pontis_root: Path to .pontis directory
        file_rel_path: Relative path to the physical file
        entity_pattern: Glob pattern for entities (e.g., "*.table", "users.*.col")

    Returns:
        List of (entity_name, info_string) tuples
    """
    results = []
    entity_root = os.path.join(pontis_root, file_rel_path, "_entity")

    if not os.path.exists(entity_root):
        return results

    # Walk entity directory tree
    for root, dirs, files in os.walk(entity_root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for dir_name in dirs:
            # Check if directory name matches entity pattern
            if fnmatch.fnmatch(dir_name, entity_pattern):
                entity_rel = os.path.relpath(os.path.join(root, dir_name), entity_root)
                # Build display name: file::entity
                display = f"{file_rel_path}::{entity_rel}"
                info = _get_entity_info(pontis_root, file_rel_path, entity_rel)
                results.append((display, info))

    return results


def _get_entity_info(pontis_root: str, file_rel_path: str, entity_rel: str) -> str:
    """Get brief info string for an entity."""
    import yaml

    entity_dir = os.path.join(pontis_root, file_rel_path, "_entity", entity_rel)
    meta_path = os.path.join(entity_dir, "_meta.yml")

    if not os.path.exists(meta_path):
        return "-"

    try:
        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return "-"

    node_type = meta.get('type', '')
    name = os.path.basename(entity_rel)

    # Determine file type from name
    file_type = get_file_type_from_name(name, node_type)
    config = get_type_config(file_type)

    from tool_use.utils.formatters import format_info_from_meta
    info = format_info_from_meta(meta, config)

    # Also try to get brief
    brief = meta.get("brief", "")
    if brief:
        return f"{info}, {brief}" if info != "-" else brief
    return info


def _get_file_info(project_path: str, file_rel_path: str) -> str:
    """Get brief info for a physical file using INFO_TYPE_CONFIG."""
    import yaml
    from tool_use.utils.formatters import get_type_config, format_info_from_meta, _format_file_size

    full_path = os.path.join(project_path, file_rel_path)

    if os.path.isdir(full_path):
        try:
            children = [e for e in os.listdir(full_path) if not e.startswith('.')]
            config = get_type_config("directory")
            meta = {"child_count": len(children)}
            return format_info_from_meta(meta, config)
        except Exception:
            return "-"

    # Read meta from .pontis
    pontis_root = os.path.join(project_path, ".pontis")
    meta_path = os.path.join(pontis_root, file_rel_path, "_meta.yml")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r') as f:
                meta = yaml.safe_load(f) or {}
        except Exception:
            pass

    # Enrich with file_size if not in meta
    if "file_size" not in meta:
        try:
            meta["file_size"] = os.path.getsize(full_path)
        except Exception:
            pass

    # Use INFO_TYPE_CONFIG template
    ext = os.path.splitext(file_rel_path)[1].lower()
    config = get_type_config(ext)
    info = format_info_from_meta(meta, config)

    # Append brief if available
    brief = meta.get("brief", "")
    if brief:
        return f"{info}, {brief}"
    return info


def glob_command(
    project_path: str,
    path_pattern: str,
    current_cwd: str = ""
) -> str:
    """
    Search physical files and their logical entities using path::entity patterns.

    Args:
        project_path: Path to project root (containing .pontis)
        path_pattern: Glob pattern, optionally with ::entity suffix
        current_cwd: Current working directory

    Returns:
        Formatted results: [name] | [Info]
    """
    parsed = parse_path_pattern(path_pattern)
    pontis_root = os.path.join(project_path, ".pontis")

    # Step 1: Glob physical files
    file_matches = _glob_physical_files(project_path, parsed.file_pattern, current_cwd)

    if not file_matches:
        return "No objects found"

    results = []
    truncated = len(file_matches) > MAX_RESULTS

    for file_rel in file_matches[:MAX_RESULTS]:
        if parsed.has_entity and os.path.exists(pontis_root):
            # Step 2: Glob entities within each matched file
            entity_results = _glob_entities_in_file(
                pontis_root, file_rel, parsed.entity_pattern
            )
            if entity_results:
                for display, info in entity_results:
                    results.append(f"{display} | {info}")
            else:
                # No entities matched, show file itself
                info = _get_file_info(project_path, file_rel)
                results.append(f"{file_rel} | {info}")
        else:
            # No entity pattern, show physical files
            info = _get_file_info(project_path, file_rel)
            results.append(f"{file_rel} | {info}")

    if not results:
        return "No objects found"

    output = "\n".join(results)

    if truncated:
        output += "\n(Results are truncated. Consider using a more specific path or pattern.)"

    return output


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.glob.tool <project_path> <path_pattern> [cwd]")
        sys.exit(1)

    _project = sys.argv[1]
    _pattern = sys.argv[2]
    _cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(glob_command(_project, _pattern, _cwd))
