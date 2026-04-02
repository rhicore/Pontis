"""Tool functions for LLM agents"""
import os
from typing import List, Optional, Dict, Any

from .vfs import PontisVFS


class ToolContext:
    """Context for tool execution, maintaining current working directory"""

    def __init__(self, pontis_root: str, cwd: str = ""):
        self.vfs = PontisVFS(pontis_root)
        self.cwd = cwd  # Current working directory (relative to pontis_root)

    def resolve_path(self, path: str) -> str:
        """Resolve a path relative to current working directory"""
        if not path or path == ".":
            return self.cwd

        if path.startswith("/"):
            return path[1:]  # Remove leading slash

        return os.path.normpath(os.path.join(self.cwd, path))


def ls(path: str = ".", context: Optional[ToolContext] = None) -> str:
    """
    List contents of a virtual directory.

    Args:
        path: Path to list (relative or absolute within pontis)
        context: Tool execution context

    Returns:
        Formatted listing of directory contents
    """
    if context is None:
        return "Error: No context provided"

    try:
        resolved_path = context.resolve_path(path)
        nodes = context.vfs.list_directory(resolved_path)

        if not nodes:
            return f"Directory '{path}' is empty"

        return context.vfs.format_ls_output(nodes)

    except FileNotFoundError:
        return f"Error: Path not found: {path}"
    except NotADirectoryError:
        return f"Error: Not a directory: {path}"
    except Exception as e:
        return f"Error listing directory: {e}"


def meta(path: str, key: Optional[str] = None, context: Optional[ToolContext] = None) -> str:
    """
    Get metadata of a virtual node (token-efficient format for LLM).

    Args:
        path: Path to the node (relative or absolute within pontis)
        key: Specific metadata key to retrieve (optional)
        context: Tool execution context

    Returns:
        Node metadata in compact text format
    """
    if context is None:
        return "Error: No context provided"

    try:
        resolved_path = context.resolve_path(path)
        node = context.vfs.get_node_info(resolved_path)

        if key:
            # Return specific key value (compact)
            value = node.raw_meta.get(key)
            if value is None:
                return f"{key}: N/A"
            # For joins, use compact format
            if key == "joins" and isinstance(value, list):
                return context.vfs.format_joins_compact(value)
            return f"{key}: {value}"
        else:
            # Return all metadata (compact format)
            return context.vfs.format_meta_compact(node)

    except FileNotFoundError:
        return f"Error: Node not found: {path}"
    except Exception as e:
        return f"Error: {e}"


def search(query: str, path: str = ".", context: Optional[ToolContext] = None) -> str:
    """
    Search for nodes by keyword in name or summary.

    Args:
        query: Search query string
        path: Starting path for search
        context: Tool execution context

    Returns:
        Formatted search results
    """
    if context is None:
        return "Error: No context provided"

    try:
        resolved_path = context.resolve_path(path)
        results = context.vfs.search_nodes(query, resolved_path)

        if not results:
            return f"No results found for '{query}'"

        lines = [f"Found {len(results)} result(s) for '{query}':\n"]

        for result in results:
            short = result.get('short_summary', '')
            if len(short) > 60:
                short = short[:57] + "..."

            lines.append(f"  [{result['type']}] {result['path']}")
            lines.append(f"      {short}\n")

        return "\n".join(lines)

    except Exception as e:
        return f"Error searching: {e}"


def find(pattern: str, path: str = ".", context: Optional[ToolContext] = None) -> str:
    """
    Find nodes by glob pattern.

    Args:
        pattern: Glob pattern (e.g., "*.db", "sales_*")
        path: Starting path for search
        context: Tool execution context

    Returns:
        List of matching paths
    """
    if context is None:
        return "Error: No context provided"

    try:
        resolved_path = context.resolve_path(path)
        results = context.vfs.find_nodes(pattern, resolved_path)

        if not results:
            return f"No matches found for pattern '{pattern}'"

        lines = [f"Found {len(results)} match(es) for '{pattern}':\n"]
        for r in results:
            lines.append(f"  {r}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error finding nodes: {e}"


def cd(path: str, context: Optional[ToolContext] = None) -> str:
    """
    Change current working directory.

    Args:
        path: Path to change to
        context: Tool execution context

    Returns:
        New working directory or error message
    """
    if context is None:
        return "Error: No context provided"

    try:
        resolved_path = context.resolve_path(path)
        full_path = os.path.join(context.vfs.pontis_root, resolved_path)

        if not os.path.exists(full_path):
            return f"Error: Directory not found: {path}"

        if not os.path.isdir(full_path):
            return f"Error: Not a directory: {path}"

        context.cwd = resolved_path
        return f"Changed to: /{resolved_path}" if resolved_path else "Changed to: /"

    except Exception as e:
        return f"Error changing directory: {e}"


def pwd(context: Optional[ToolContext] = None) -> str:
    """
    Print current working directory.

    Args:
        context: Tool execution context

    Returns:
        Current working directory path
    """
    if context is None:
        return "Error: No context provided"

    cwd = context.cwd
    return f"/{cwd}" if cwd else "/"


# Tool definitions for LLM function calling
TOOL_DEFINITIONS = [
    {
        "name": "ls",
        "description": "List contents of a virtual directory in the Pontis VFS",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to list (relative or absolute within pontis). Use '.' for current directory."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "meta",
        "description": "Get metadata of a specific node (file, table, column, etc.) in the Pontis VFS. Optionally retrieve a specific key.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the node (relative or absolute within pontis)"
                },
                "key": {
                    "type": "string",
                    "description": "Optional: specific metadata key to retrieve (e.g., 'row_count', 'data_type'). If omitted, returns all metadata."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "search",
        "description": "Search for nodes by keyword in name or description",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string to match against node names and summaries"
                },
                "path": {
                    "type": "string",
                    "description": "Starting path for search (default: root)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "find",
        "description": "Find nodes by glob pattern (e.g., '*.db', 'sales_*')",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match node names"
                },
                "path": {
                    "type": "string",
                    "description": "Starting path for search (default: root)"
                }
            },
            "required": ["pattern"]
        }
    }
]


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Get tool definitions for LLM function calling"""
    return TOOL_DEFINITIONS
