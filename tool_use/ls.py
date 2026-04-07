"""ls command - List directory contents

Usage:
    python -m tool_use.ls <pontis_path> [directory]
"""
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext
from tool_use.utils.formatters import can_ls_node, get_type_config, get_file_type_from_name


def ls_command(pontis_root: str, path: str = ".", current_cwd: str = "") -> str:
    """
    List contents of a virtual directory.
    Only nodes with has_sub=[+] can be listed.

    Args:
        pontis_root: Path to .pontis directory
        path: Path to list (relative or absolute within pontis)
        current_cwd: Current working directory (for relative paths)

    Returns:
        Formatted listing of directory contents
    """
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        # Check if the target path can be listed
        resolved_path = ctx.resolve_path(path)
        try:
            node = ctx.vfs.get_node_info(resolved_path)

            # Check if node has children (has_sub=[+])
            has_children = getattr(node, 'has_children', False)

            # Get type config to check if ls is allowed
            # Use display_name if available (for serialized nodes), otherwise use name
            display_name = getattr(node, 'display_name', getattr(node, 'name', ''))
            if display_name:
                node_type = getattr(node, 'node_type', '')
                file_type = get_file_type_from_name(display_name, str(node_type))
                config = get_type_config(file_type)

                if not can_ls_node(has_children, config):
                    return f"Error: '{path}' has no children (cannot list)"

        except Exception:
            # If we can't get node info, let the original ls handle the error
            pass

        # Direct implementation
        resolved_path = ctx.resolve_path(path)
        nodes = ctx.vfs.list_directory(resolved_path)

        if not nodes:
            return f"'{path}' has no children"

        return ctx.vfs.format_ls_output(nodes)
    except Exception as e:
        return f"Error listing directory: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tool_use.ls <pontis_path> [directory]")
        print("Example: python -m tool_use.ls ./.pontis mydb.db")
        sys.exit(1)

    pontis_path = sys.argv[1]
    directory = sys.argv[2] if len(sys.argv) > 2 else "."
    print(ls_command(pontis_path, directory))
