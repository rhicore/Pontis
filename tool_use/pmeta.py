"""pmeta command - Read/Write metadata for physical files and knowledge graph entities

Usage for physical file attributes:
    python -m tool_use.pmeta <project_path> <physical_file> [-a] [+key] [key=value]

Usage for entity attributes:
    python -m tool_use.pmeta <project_path> <physical_file> <entity_name> [-a] [+key] [key=value]

Examples:
    # Show physical file metadata
    python -m tool_use.pmeta ./my_project mydb.db
    python -m tool_use.pmeta ./my_project mydb.db -a
    python -m tool_use.pmeta ./my_project mydb.db +table_count

    # Show entity metadata
    python -m tool_use.pmeta ./my_project mydb.db users.table
    python -m tool_use.pmeta ./my_project mydb.db users.id.INT.col +cardinality

    # Write metadata (physical file)
    python -m tool_use.pmeta ./my_project mydb.db "semantic_summary=User database"

    # Write metadata (entity)
    python -m tool_use.pmeta ./my_project mydb.db users.table "description=User info"
"""
import sys
import os
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def parse_pmeta_args(args: list) -> tuple:
    """Parse pmeta command arguments.

    Returns: (entity_name, show_all, specific_key, write_key, write_value)
    """
    entity_name = None
    show_all = False
    specific_key = None
    write_key = None
    write_value = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "-a":
            show_all = True
        elif arg.startswith("+"):
            specific_key = arg[1:]
        elif "=" in arg:
            # Write operation: key=value
            parts = arg.split("=", 1)
            write_key = parts[0]
            write_value = parts[1] if len(parts) > 1 else ""
            # Try to parse as number/bool
            try:
                if "." in write_value:
                    write_value = float(write_value)
                else:
                    write_value = int(write_value)
            except ValueError:
                if write_value.lower() == "true":
                    write_value = True
                elif write_value.lower() == "false":
                    write_value = False
                elif write_value.lower() == "null" or write_value.lower() == "none":
                    write_value = None
        else:
            # This is likely the entity name (first non-flag arg after physical file)
            if entity_name is None:
                entity_name = arg
            else:
                # Second non-flag arg - treat as value for write
                pass
        i += 1

    return entity_name, show_all, specific_key, write_key, write_value


def find_meta_path(pontis_root: str, physical_file: str, entity_name: str = None) -> str:
    """Find the path to _meta.yml for a physical file or entity.

    Args:
        pontis_root: Path to .pontis directory
        physical_file: Path to physical file (e.g., "mydb.db")
        entity_name: Optional entity name (e.g., "users.table")

    Returns:
        Path to _meta.yml file
    """
    if entity_name:
        # Entity meta is under the physical file directory
        meta_dir = os.path.join(pontis_root, physical_file, entity_name)
    else:
        # Physical file meta is in the file's directory
        meta_dir = os.path.join(pontis_root, physical_file)

    return os.path.join(meta_dir, "_meta.yml")


def read_meta(meta_path: str) -> dict:
    """Read metadata from _meta.yml."""
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def write_meta(meta_path: str, meta: dict):
    """Write metadata to _meta.yml."""
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, 'w') as f:
        yaml.dump(meta, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def format_meta_output(meta: dict, show_all: bool = False, specific_key: str = None) -> str:
    """Format metadata for display."""
    if not meta:
        return "No metadata found"

    if specific_key:
        # Show only specific key
        if specific_key in meta:
            value = meta[specific_key]
            return f"{specific_key}: {value}"
        return f"Key '{specific_key}' not found"

    lines = []

    if show_all:
        # Show all metadata
        for key, value in meta.items():
            lines.append(f"{key}: {value}")
    else:
        # Show key fields in order
        key_fields = [
            'path', 'type', 'created_at', 'modified_at',
            'table_count', 'view_count', 'index_count',
            'row_count', 'column_count', 'primary_key',
            'cardinality', 'null_count', 'null_percentage',
            'min_value', 'max_value', 'mean_value',
            'semantic_summary', 'short_summary'
        ]

        shown = set()
        for key in key_fields:
            if key in meta and meta[key] is not None:
                value = meta[key]
                if isinstance(value, list):
                    if len(value) > 5:
                        value = str(value[:5])[:-1] + f", ... ({len(value)} items)]"
                lines.append(f"{key}: {value}")
                shown.add(key)

        # Show remaining fields
        for key, value in meta.items():
            if key not in shown and value is not None:
                if key in ['sample', 'topk']:
                    if isinstance(value, list):
                        lines.append(f"{key}: [{len(value)} items]")
                    else:
                        lines.append(f"{key}: {value}")
                else:
                    lines.append(f"{key}: {value}")

    return "\n".join(lines)


def pmeta_command(
    project_path: str,
    physical_file: str,
    args: list,
    current_cwd: str = ""
) -> str:
    """Get/Set metadata for physical files and entities."""
    pontis_root = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        # Resolve physical file path
        resolved_physical = ctx.resolve_path(physical_file)

        # Parse arguments
        entity_name, show_all, specific_key, write_key, write_value = parse_pmeta_args(args)

        # Find meta file path
        meta_path = find_meta_path(pontis_root, resolved_physical, entity_name)

        # Handle write operation
        if write_key is not None:
            meta = read_meta(meta_path)
            meta[write_key] = write_value
            write_meta(meta_path, meta)
            target = f"{physical_file}/{entity_name}" if entity_name else physical_file
            return f"Updated {target}: {write_key} = {write_value}"

        # Handle read operation
        meta = read_meta(meta_path)

        if not meta:
            target = f"{physical_file}/{entity_name}" if entity_name else physical_file
            return f"No metadata found for '{target}'"

        return format_meta_output(meta, show_all, specific_key)

    except Exception as e:
        return f"Error accessing metadata: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    project_path = sys.argv[1]
    physical_file = sys.argv[2]
    args = sys.argv[3:]

    print(pmeta_command(project_path, physical_file, args))
