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
from typing import Optional

from tool_use.utils.path_parser import parse_path_pattern
from tool_use.utils.formatters import get_meta_type_config, format_meta_output


def meta_command(
    store,
    path: str,
    all: bool = False,
    property: Optional[str] = None,
    current_cwd: str = ""
) -> str:
    """
    View metadata for a physical file/directory or logical entity.

    Args:
        store: Store instance
        path: path::entity string
        all: Whether to show all metadata
        property: Specific property to view
        current_cwd: Current working directory

    Returns:
        Formatted metadata
    """
    parsed = parse_path_pattern(path)

    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    # Resolve path
    if current_cwd and not os.path.isabs(parsed.file_pattern):
        resolved_file = os.path.join(current_cwd, parsed.file_pattern)
    else:
        resolved_file = parsed.file_pattern

    # Get metadata (enriched with virtual props)
    if parsed.has_entity:
        ref = f"{resolved_file}::{parsed.entity_pattern}"
        meta = store.get_meta(ref)
    else:
        meta = store.get_meta(resolved_file)

    target = f"{resolved_file}::{parsed.entity_pattern}" if parsed.has_entity else resolved_file

    if meta is None:
        # Fallback: list directory contents
        if os.path.isdir(os.path.join(store.project_path, resolved_file)):
            entries = [e for e in os.listdir(os.path.join(store.project_path, resolved_file)) if not e.startswith('.')]
            if entries:
                return f"No metadata found for '{target}'. Directory contains: {entries[:10]}"
        return f"No metadata found for '{target}'"

    if not meta:
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
    if parsed.has_entity:
        entity_name = os.path.basename(parsed.entity_pattern)
        if "." in entity_name:
            file_ext = "." + entity_name.split(".")[-1].lower()
        else:
            file_ext = ""
    else:
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
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.meta.tool <project_path> <path> [--all] [+property]")
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
