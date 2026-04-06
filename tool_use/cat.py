"""cat command - Show content of a node (for serialized files)

Usage:
    python -m tool_use.cat <pontis_path> <node_path>
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def cat_command(pontis_root: str, path: str) -> str:
    """
    Show content of a node (for scalar values in serialized files).

    Args:
        pontis_root: Path to .pontis directory
        path: Path to the node

    Returns:
        Node content or metadata
    """
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        ctx = ToolContext(pontis_root)
        resolved_path = ctx.resolve_path(path)
        node = ctx.vfs.get_node_info(resolved_path)

        # For serialized nodes
        if hasattr(node, 'get_content'):
            content = node.get_content()
            if content:
                return content
            else:
                return f"<{node.node_type.value}> (use 'ls' to see children)"
        else:
            # For regular nodes, show metadata
            from tool_use.utils.tools import meta
            return meta(path, None, ctx)
    except Exception as e:
        return f"Error showing content: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.cat <pontis_path> <node_path>")
        print("Example: python -m tool_use.cat ./.pontis config.json/ROOT.DICT/host.STR")
        sys.exit(1)

    pontis_path = sys.argv[1]
    node_path = sys.argv[2]
    print(cat_command(pontis_path, node_path))
