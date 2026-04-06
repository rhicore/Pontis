"""find command - Find nodes by glob pattern

Usage:
    python -m tool_use.find <pontis_path> <pattern> [path]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def find_command(pontis_root: str, pattern: str, path: str = ".") -> str:
    """
    Find nodes by glob pattern.

    Args:
        pontis_root: Path to .pontis directory
        pattern: Glob pattern (e.g., "*.db", "sales_*")
        path: Starting path for search

    Returns:
        List of matching paths
    """
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        ctx = ToolContext(pontis_root)
        return find(pattern, path, ctx)
    except Exception as e:
        return f"Error finding nodes: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.find <pontis_path> <pattern> [path]")
        print("Example: python -m tool_use.find ./.pontis '*.db'")
        sys.exit(1)

    pontis_path = sys.argv[1]
    pattern = sys.argv[2]
    path = sys.argv[3] if len(sys.argv) > 3 else "."
    print(find_command(pontis_path, pattern, path))
