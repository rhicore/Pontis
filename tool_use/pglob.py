"""pglob command - Search knowledge graph entities under a physical file

Usage:
    python -m tool_use.pglob <project_path> <physical_file> <entity_pattern>

Examples:
    python -m tool_use.pglob ./my_project dev_databases/financial/financial.db "*.table"
    python -m tool_use.pglob ./my_project dev_databases/financial/financial.db "account.*.col"
    python -m tool_use.pglob ./my_project dev.json "ROOT.DICT.*"
"""
import sys
import os
import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext
from tool_use.utils.formatters import get_type_config, get_file_type_from_name, get_brief_from_meta
from tool_use.utils.vfs import PontisVFS
from tool_use.utils.serialized_vfs import SerializedNode


def pglob_command(project_path: str, physical_file: str, entity_pattern: str, current_cwd: str = "") -> str:
    """
    Search knowledge graph entities under a physical file.

    Args:
        project_path: Path to project directory (containing .pontis)
        physical_file: Path to physical file (e.g., "mydb.db" or "data.json")
        entity_pattern: Glob pattern for entities (e.g., "*.table", "users.*.col")
        current_cwd: Current working directory for relative paths

    Returns:
        Formatted listing of matching entities
    """
    pontis_root = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        # Resolve physical file path
        resolved_physical = ctx.resolve_path(physical_file)

        # Check if physical file exists in pontis
        physical_full_path = os.path.join(pontis_root, resolved_physical)
        if not os.path.exists(physical_full_path):
            return f"Error: Physical file not found: {physical_file}"

        # Get the directory containing the physical file (for .db, .csv, etc.)
        # The entities are stored as subdirectories under the physical file
        if os.path.isdir(physical_full_path):
            # For directories (like .db which is a directory in pontis)
            search_root = resolved_physical
        else:
            # For files (shouldn't happen in current design)
            return f"Error: Not a container: {physical_file}"

        # Find all entities matching the pattern
        vfs = PontisVFS(pontis_root)
        matching_nodes = []

        # Walk the directory structure under the physical file
        search_full_path = os.path.join(pontis_root, search_root)
        for root, dirs, _ in os.walk(search_full_path):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for dir_name in dirs:
                # Check if directory name matches the pattern
                if fnmatch.fnmatch(dir_name, entity_pattern):
                    rel_path = os.path.relpath(os.path.join(root, dir_name), pontis_root)
                    try:
                        node = vfs.get_node_info(rel_path)
                        matching_nodes.append(node)
                    except:
                        pass

        if not matching_nodes:
            return f"No entities matching '{entity_pattern}' found under {physical_file}"

        # Format output using ls-style formatting
        return format_pglob_output(matching_nodes)

    except Exception as e:
        return f"Error searching entities: {e}"


def format_pglob_output(nodes: list) -> str:
    """Format nodes for pglob output using ls-style format."""
    if not nodes:
        return "(empty)"

    # Sort nodes: directories first, then by name
    def sort_key(n):
        has_sub = getattr(n, 'has_children', False) or getattr(n, 'node_type', '') in ['Table', 'Column', 'View']
        return (not has_sub, n.name.lower())

    sorted_nodes = sorted(nodes, key=sort_key)

    lines = []
    lines.append("[Type]     | [Name]                           | [Info]               | [Brief]")
    lines.append("-" * 85)

    for node in sorted_nodes:
        # Determine node type display
        node_type = getattr(node, 'node_type', 'Unknown')
        if isinstance(node, SerializedNode):
            node_type = node.node_type.value

        # Format type column
        type_str = node_type[:10]

        # Format name
        name = node.name
        if len(name) > 32:
            name = name[:29] + "..."

        # Format info based on node type
        info = format_node_info(node)
        if len(info) > 20:
            info = info[:17] + "..."

        # Format brief
        brief = ""
        if hasattr(node, 'raw_meta'):
            file_type = get_file_type_from_name(node.name, node.node_type)
            config = get_type_config(file_type)
            brief = get_brief_from_meta(node.raw_meta, config)
        if len(brief) > 25:
            brief = brief[:22] + "..."

        line = f"{type_str:<10} | {name:<32} | {info:<20} | {brief}"
        lines.append(line)

    return "\n".join(lines)


def format_node_info(node) -> str:
    """Format info field based on node type."""
    node_type = getattr(node, 'node_type', '')

    if node_type == 'Table':
        rows = getattr(node, 'row_count', None)
        cols = getattr(node, 'column_count', None)
        if rows is not None and cols is not None:
            return f"{rows} rows, {cols} cols"

    elif node_type == 'Column':
        card = getattr(node, 'cardinality', None)
        if card is not None:
            return f"distinct: {card}"
        null_pct = getattr(node, 'null_percentage', None)
        if null_pct is not None:
            return f"null: {null_pct:.1f}%"

    elif node_type == 'Database':
        tables = getattr(node, 'table_count', None)
        views = getattr(node, 'view_count', None)
        if tables is not None:
            return f"{tables} tables, {views} views"

    elif node_type == 'View':
        return "view"

    elif isinstance(node, SerializedNode):
        return node.get_info()

    return "-"


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        print("\nExamples:")
        print("  python -m tool_use.pglob ./my_project mydb.db '*.table'")
        print("  python -m tool_use.pglob ./my_project mydb.db 'users.*.col'")
        sys.exit(1)

    project_path = sys.argv[1]
    physical_file = sys.argv[2]
    entity_pattern = sys.argv[3]

    print(pglob_command(project_path, physical_file, entity_pattern))
