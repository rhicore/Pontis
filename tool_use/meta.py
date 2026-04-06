"""meta command - Get metadata of a node

Usage:
    python -m tool_use.meta <pontis_path> <node_path> [key]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def meta_command(pontis_root: str, path: str, key: str = None) -> str:
    """
    Get metadata of a virtual node.

    Args:
        pontis_root: Path to .pontis directory
        path: Path to the node
        key: Specific metadata key (optional)

    Returns:
        Node metadata
    """
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        ctx = ToolContext(pontis_root)
        return meta(path, key, ctx)
    except Exception as e:
        return f"Error getting metadata: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.meta <pontis_path> <node_path> [key]")
        print("Example: python -m tool_use.meta ./.pontis mydb.db/users.table")
        sys.exit(1)

    pontis_path = sys.argv[1]
    node_path = sys.argv[2]
    key = sys.argv[3] if len(sys.argv) > 3 else None
    print(meta_command(pontis_path, node_path, key))
