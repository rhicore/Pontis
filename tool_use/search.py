"""search command - Search for nodes by keyword

Usage:
    python -m tool_use.search <pontis_path> <query> [path]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def search_command(pontis_root: str, query: str, path: str = ".") -> str:
    """
    Search for nodes by keyword.

    Args:
        pontis_root: Path to .pontis directory
        query: Search query string
        path: Starting path for search

    Returns:
        Formatted search results
    """
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        ctx = ToolContext(pontis_root)
        return search(query, path, ctx)
    except Exception as e:
        return f"Error searching: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.search <pontis_path> <query> [path]")
        print("Example: python -m tool_use.search ./.pontis user")
        sys.exit(1)

    pontis_path = sys.argv[1]
    query = sys.argv[2]
    path = sys.argv[3] if len(sys.argv) > 3 else "."
    print(search_command(pontis_path, query, path))
