"""meta command - Get metadata from _meta.yml

Usage:
    python -m tool_use.meta <pontis_path> <node_path> [-a] [+key]

Options:
    -a          Show all attributes from _meta.yml
    +key        Show specific attribute only
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml
from tool_use.utils.context import ToolContext
from tool_use.utils.formatters import get_meta_type_config, format_meta_output


def parse_meta_args(args: list) -> tuple:
    """Parse meta command arguments"""
    show_all = False
    specific_key = None
    path_parts = []

    for arg in args:
        if arg == "-a":
            show_all = True
        elif arg.startswith("+"):
            specific_key = arg[1:]
        else:
            path_parts.append(arg)

    path = " ".join(path_parts) if path_parts else "."
    return path, show_all, specific_key


def _infer_node_type(meta_dict: dict) -> str:
    """Infer node type from available fields in _meta.yml"""
    fields = set(meta_dict.keys())

    # Check for Column indicators
    if 'cardinality' in fields or 'null_percentage' in fields:
        return 'Column'

    # Check for Table indicators
    if 'row_count' in fields and 'column_count' in fields:
        return 'Table'

    # Check for View indicators
    if 'column_count' in fields and 'row_count' not in fields:
        return 'View'

    # Check for Database indicators
    if 'table_count' in fields or 'view_count' in fields:
        return 'Database'

    # Check for Serialized file indicators
    if 'structure_type' in fields or 'array_length' in fields:
        return 'Serialized'

    # Check for Document indicators
    if 'char_count' in fields and 'line_count' in fields:
        return 'Document'

    return 'default'


def read_meta_yml(pontis_root: str, node_path: str) -> dict:
    """Read _meta.yml for the given node path"""
    # Build path to _meta.yml
    if node_path.endswith('.json') or node_path.endswith('.yaml') or node_path.endswith('.yml'):
        # For serialized files, _meta.yml is in the directory
        meta_path = os.path.join(pontis_root, node_path, '_meta.yml')
    else:
        # For other nodes, look in the node directory
        meta_path = os.path.join(pontis_root, node_path, '_meta.yml')

    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def meta_command(
    pontis_root: str,
    args: list,
    current_cwd: str = ""
) -> str:
    """Get metadata from _meta.yml"""
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        # Parse arguments
        path, show_all, specific_key = parse_meta_args(args)

        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd
        resolved_path = ctx.resolve_path(path)

        # Read _meta.yml directly
        meta_dict = read_meta_yml(pontis_root, resolved_path)

        if not meta_dict:
            return f"No metadata found for '{path}'"

        # Get type for config - infer from fields if not explicitly set
        node_type = meta_dict.get('type')
        if not node_type:
            node_type = _infer_node_type(meta_dict)
        config = get_meta_type_config(node_type)

        # Format output
        return format_meta_output(meta_dict, config, show_all, specific_key)

    except Exception as e:
        return f"Error getting metadata: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nExamples:")
        print("  python -m tool_use.meta ./.pontis mydb.db")
        print("  python -m tool_use.meta ./.pontis mydb.db -a")
        print("  python -m tool_use.meta ./.pontis mydb.db +row_count")
        sys.exit(1)

    pontis_path = sys.argv[1]
    args = sys.argv[2:]
    print(meta_command(pontis_path, args))
