"""
Meta tool - View metadata for file/directory/entity nodes.

Store.get_meta(ref) handles ref resolution internally:
  "event.db"              → file node (via inode)
  "event.db::users.table" → entity node
  "ent_a3f2c801"          → ID direct reference

Virtual properties are always computed and included.
"""
from typing import List, Optional, Union

from tool_use.utils.formatters import get_meta_type_config, format_meta_output


def meta_command(
    store,
    path: str,
    all: bool = False,
    property: Optional[Union[str, List[str]]] = None,
    current_cwd: str = ""
) -> str:
    """
    View metadata for a node.

    Args:
        store: Store instance
        path: ref string (file path, path::entity, or ent_xxx)
        all: Whether to show all metadata
        property: Specific property or list of properties to view
        current_cwd: Current working directory (unused)

    Returns:
        Formatted metadata
    """
    if not store.pontis_exists:
        return f"Error: .pontis directory not found in {store.project_path}"

    meta = store.get_meta(path)

    if meta is None:
        return f"No metadata found for '{path}'"

    if not meta:
        return f"Empty metadata for '{path}'"

    # Normalize property to list
    props = None
    if property:
        if isinstance(property, str):
            props = [property]
        else:
            props = list(property)

    # If specific property/properties requested
    if props:
        from tool_use.utils.formatters import _format_meta_value
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

    # Determine type config by extracting extension from ref
    is_entity = "::" in path
    if is_entity:
        entity_name = path.split("::", 1)[1]
        if "." in entity_name:
            file_ext = "." + entity_name.split(".")[-1].lower()
        else:
            file_ext = ""
    else:
        import os
        _, ext = os.path.splitext(path)
        file_ext = ext.lower()

    config = get_meta_type_config(file_ext)

    # Format output
    result = format_meta_output(meta, config, show_all=all, specific_key=None)

    # Fallback: if default_keys matched nothing, show all fields
    if not result and not all:
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
