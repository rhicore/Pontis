"""
Meta tool - View metadata for physical files, directories, and logical entities.

Supports path::entity syntax:
    meta "data.db"                        # File metadata
    meta "data.db::users.table"           # Entity metadata
    meta "data.db::users.id.INT.col"      # Column metadata

Parameters:
    path: path::entity string
    all: show all metadata (bool, default False)
    property: show specific property only
"""
import os
import sys
from typing import Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_use.utils.path_parser import parse_path_pattern
from tool_use.utils.formatters import get_meta_type_config, format_meta_output


def _find_meta_path(pontis_root: str, file_rel_path: str,
                    entity_path: Optional[str] = None) -> str:
    """Find the _meta.yml path for a file or entity."""
    if entity_path:
        return os.path.join(pontis_root, file_rel_path, "_entity", entity_path, "_meta.yml")
    return os.path.join(pontis_root, file_rel_path, "_meta.yml")


def _read_meta(meta_path: str) -> dict:
    """Read metadata from _meta.yml."""
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def meta_command(
    project_path: str,
    path: str,
    all: bool = False,
    property: Optional[str] = None,
    current_cwd: str = ""
) -> str:
    """
    View metadata for a physical file/directory or logical entity.

    Args:
        project_path: Path to project root
        path: path::entity string
        all: Whether to show all metadata
        property: Specific property to view
        current_cwd: Current working directory

    Returns:
        Formatted metadata
    """
    parsed = parse_path_pattern(path)
    pontis_root = os.path.join(project_path, ".pontis")

    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    # Resolve path
    if current_cwd and not os.path.isabs(parsed.file_pattern):
        resolved_file = os.path.join(current_cwd, parsed.file_pattern)
    else:
        resolved_file = parsed.file_pattern

    # Find meta file
    meta_path = _find_meta_path(pontis_root, resolved_file, parsed.entity_pattern)

    if not os.path.exists(meta_path):
        target = f"{resolved_file}::{parsed.entity_pattern}" if parsed.entity_pattern else resolved_file
        # Try fallback: if looking at a directory, try to list what's inside
        dir_path = os.path.dirname(meta_path)
        if os.path.exists(dir_path):
            entries = [e for e in os.listdir(dir_path) if not e.startswith('.') and e != '_meta.yml']
            if entries:
                return f"No metadata found for '{target}'. Directory contains: {entries[:10]}"
        return f"No metadata found for '{target}'"

    meta = _read_meta(meta_path)
    if not meta:
        target = f"{resolved_file}::{parsed.entity_pattern}" if parsed.entity_pattern else resolved_file
        return f"Empty metadata for '{target}'"

    # If specific property requested
    if property:
        value = meta.get(property)
        if value is None:
            available = sorted(meta.keys())
            return f"Property '{property}' not found. Available: {', '.join(available)}"
        from tool_use.utils.formatters import _format_meta_value
        return f"{property}: {_format_meta_value(value, None)}"

    # Get type config for formatting — purely by extension
    if parsed.entity_pattern:
        # Entity: check entity name suffix (e.g. "users.table" → ".table")
        entity_name = os.path.basename(parsed.entity_pattern)
        if "." in entity_name:
            file_ext = "." + entity_name.split(".")[-1].lower()
        else:
            file_ext = ""
    else:
        # File-level: use file extension
        file_ext = os.path.splitext(resolved_file)[1].lower()
    config = get_meta_type_config(file_ext)

    # Format output
    result = format_meta_output(meta, config, show_all=all, specific_key=property)

    # Fallback: if default_keys matched nothing, show all fields
    if not result and not all and not property:
        lines = []
        for key, value in sorted(meta.items()):
            lines.append(f"{key}: {value}")
        result = "\n".join(lines)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.meta.tool <project_path> <path> [--all] [+property]")
        sys.exit(1)

    _project = sys.argv[1]
    _path = sys.argv[2]
    _all = '--all' in sys.argv or '-a' in sys.argv
    _prop = None
    for arg in sys.argv[3:]:
        if arg.startswith('+'):
            _prop = arg[1:]

    print(meta_command(_project, _path, _all, _prop))
