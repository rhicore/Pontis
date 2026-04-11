"""Tool definitions and execution dispatcher for the agent.

Wraps tool_use/ functions into OpenAI tool calling schema.
"""
import importlib
from typing import Dict, List, Any


def _load_prompt(tool_name: str) -> str:
    """Load tool prompt from tool_use/<tool>/prompt.py."""
    try:
        mod = importlib.import_module(f"tool_use.{tool_name}.prompt")
        return mod.get_description()
    except (ImportError, AttributeError):
        return ""


# ==================== Tool Schemas ====================

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": _load_prompt("glob"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_pattern": {
                        "type": "string",
                        "description": "Glob pattern for files and entities, e.g. '**/*.db::*user*.table'",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting index (0-based), default 0",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per page, default 100",
                    },
                },
                "required": ["path_pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": _load_prompt("grep"),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern (ripgrep syntax)",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search, defaults to project root",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "Output mode, default 'files_with_matches'",
                    },
                    "glob": {
                        "type": "string",
                        "description": "File name filter, e.g. '*.py', '*.{ts,tsx}'",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "Case insensitive search",
                    },
                    "head_limit": {
                        "type": "integer",
                        "description": "Max output entries, default 250",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting index (0-based), default 0",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": _load_prompt("read"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path or path::entity to read",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Start line (1-indexed for text)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines/rows to read, default 2000",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "meta",
            "description": _load_prompt("meta"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or entity path to view metadata, e.g. 'test.db::users.table'",
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Show all metadata fields, not just defaults",
                    },
                    "property": {
                        "type": "string",
                        "description": "Show a specific property only",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": _load_prompt("lookup"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_pattern": {
                        "type": "string",
                        "description": "Glob pattern for files, e.g. '**/*.db'",
                    },
                    "type": {
                        "type": "string",
                        "description": "Data type to search: INT, TEXT, REAL, BOOL, STR",
                    },
                    "predicate": {
                        "type": "string",
                        "description": "Filter expression, e.g. 'INT > 100', 'STR = \"active\"'",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["distinct_count", "file_count"],
                        "description": "Output mode, default 'distinct_count'",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting index (0-based), default 0",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per page, default 50",
                    },
                },
                "required": ["file_pattern", "type", "predicate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": _load_prompt("search"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_pattern": {
                        "type": "string",
                        "description": "Glob pattern to scope the search",
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting index (0-based), default 0",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per page, default 100",
                    },
                },
                "required": ["path_pattern", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": _load_prompt("bash"),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds, default 120",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


# ==================== Tool Execution ====================

def execute_tool(name: str, arguments: dict, store) -> str:
    """Execute a tool by name with given arguments."""
    try:
        if name == "glob":
            from tool_use.glob.tool import glob_command
            return glob_command(
                store, arguments["path_pattern"],
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit"),
            )

        elif name == "grep":
            from tool_use.grep.tool import grep_command
            return grep_command(
                store,
                pattern=arguments["pattern"],
                path=arguments.get("path", ""),
                output_mode=arguments.get("output_mode", "files_with_matches"),
                glob=arguments.get("glob"),
                ignore_case=arguments.get("ignore_case", False),
                head_limit=arguments.get("head_limit", 250),
                offset=arguments.get("offset", 0),
            )

        elif name == "read":
            from tool_use.read.tool import read_command
            kwargs = {"store": store, "file_path": arguments["file_path"]}
            if "offset" in arguments:
                kwargs["offset"] = arguments["offset"]
            if "limit" in arguments:
                kwargs["limit"] = arguments["limit"]
            return read_command(**kwargs)

        elif name == "meta":
            from tool_use.meta.tool import meta_command
            return meta_command(
                store,
                path=arguments["path"],
                all=arguments.get("all", False),
                property=arguments.get("property"),
            )

        elif name == "lookup":
            from tool_use.lookup.tool import lookup_command
            return lookup_command(
                store,
                file_pattern=arguments["file_pattern"],
                type=arguments["type"],
                predicate=arguments["predicate"],
                output_mode=arguments.get("output_mode", "distinct_count"),
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit"),
            )

        elif name == "search":
            from tool_use.search.tool import search_command
            return search_command(
                store,
                path_pattern=arguments["path_pattern"],
                query=arguments["query"],
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit"),
            )

        elif name == "bash":
            from tool_use.bash.tool import bash_command
            return bash_command(
                command=arguments["command"],
                cwd=store.project_path,
                timeout_ms=arguments.get("timeout", 120) * 1000,
            )

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {type(e).__name__}: {e}"
