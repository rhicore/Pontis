"""Tool definitions and execution dispatcher for the agent.

Uses ToolRegistry pattern: tools are registered with (schema, executor) pairs,
allowing each agent instance to use an explicit tool set.
"""
import importlib
from typing import Callable, Dict, List, Tuple


# tool_name → directory name in agent/tool_use/ (when they differ)
_TOOL_DIR_MAP = {
    "agent": "sub_agent",
}


def _load_prompt(tool_name: str) -> str:
    """Load tool prompt from agent/tool_use/{dir}/prompt.py."""
    try:
        dir_name = _TOOL_DIR_MAP.get(tool_name, tool_name)
        mod_path = f"agent.tool_use.{dir_name}.prompt"
        mod = importlib.import_module(mod_path)
        return mod.get_description()
    except (ImportError, AttributeError):
        return ""


# ==================== Tool Registry ====================

class ToolRegistry:
    """工具注册表，支持组合不同 agent 实例的工具集。"""

    def __init__(self):
        self._tools: Dict[str, Tuple[dict, Callable]] = {}

    def register(self, name: str, schema: dict, executor: Callable):
        """注册工具: name → (OpenAI function schema, executor(store, arguments) -> str)"""
        self._tools[name] = (schema, executor)

    def get_definitions(self) -> List[dict]:
        """返回 OpenAI tool calling schema 列表。"""
        return [schema for schema, _ in self._tools.values()]

    def execute(self, name: str, arguments: dict, workspace) -> str:
        """执行工具调用。"""
        if name not in self._tools:
            return f"Unknown tool: {name}"
        _, executor = self._tools[name]
        try:
            return executor(workspace, arguments)
        except Exception as e:
            return f"Tool error ({name}): {type(e).__name__}: {e}"

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())


# ==================== Tool Schemas ====================

def _build_readonly_schemas() -> Dict[str, dict]:
    """构建只读工具的 OpenAI function schema。"""
    return {
        "find": {
            "type": "function",
            "function": {
                "name": "find",
                "description": _load_prompt("find"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "Graph ref pattern with entity_name:label path segments, e.g. '*:file', '*:col', 'results.db:db/*:table', 'results.db:db/yearmonth:table/*:col', 'bird::*:example'",
                        },
                        "query": {
                            "type": "string",
                            "description": "Natural language search query, e.g. 'track number'",
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
                    "required": ["ref"],
                },
            },
        },
        "grep": {
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
                        "ref": {
                            "type": "string",
                            "description": "Text file or directory graph ref to search, e.g. '*:file:text' or 'context/notes.md:file:text'",
                        },
                        "output_mode": {
                            "type": "string",
                            "enum": ["content", "files_with_matches", "count"],
                            "description": "Output mode, default 'files_with_matches'",
                        },
                        "file_pattern": {
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
                    "required": ["pattern", "ref"],
                },
            },
        },
        "read": {
            "type": "function",
            "function": {
                "name": "read",
                "description": _load_prompt("read"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "Text file graph ref, e.g. 'README.md:file:text'",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Start line number, default 1",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "End line number, capped by tool limit",
                        },
                    },
                    "required": ["ref"],
                },
            },
        },
        "jd": {
            "type": "function",
            "function": {
                "name": "jd",
                "description": _load_prompt("jd"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "JSON file graph ref or JSON VFS ref, e.g. 'data.json:file:json#/records/0'",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max child items to display, default 50",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting child offset, default 0",
                        },
                        "max_value_chars": {
                            "type": "integer",
                            "description": "Max preview length for scalar values",
                        },
                    },
                    "required": ["ref"],
                },
            },
        },
        "meta": {
            "type": "function",
            "function": {
                "name": "meta",
                "description": _load_prompt("meta"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "Graph ref copied from find output or composed from meta Related as parent_ref/neighbor_name:neighbor_label",
                        },
                        "all": {
                            "type": "boolean",
                            "description": "Show all metadata fields, not just defaults",
                        },
                        "property": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}}
                            ],
                            "description": "Show specific property or list of properties when explicitly needed",
                        },
                    },
                    "required": ["ref"],
                },
            },
        },
        "bash": {
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
        "query": {
            "type": "function",
            "function": {
                "name": "query",
                "description": _load_prompt("query"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "SQL query statement (SELECT only)",
                        },
                        "ref": {
                            "type": "string",
                            "description": "DB/CSV/TSV file graph ref, e.g. 'data.db:file:db' or 'data.csv:file:csv:text'",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max rows to return, default 20",
                        },
                    },
                    "required": ["sql", "ref"],
                },
            },
        },
        "cypher": {
            "type": "function",
            "function": {
                "name": "cypher",
                "description": _load_prompt("cypher"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Cypher query statement",
                        },
                        "project": {
                            "type": "string",
                            "description": "Optional project route. One Cypher call executes against one project database.",
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
                    "required": ["query"],
                },
            },
        },
    }


def _build_write_schemas() -> Dict[str, dict]:
    """构建写入工具的 OpenAI function schema。"""
    return {
        "create_entity": {
            "type": "function",
            "function": {
                "name": "create_entity",
                "description": _load_prompt("create_entity"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "实体引用，如 'account.id->district.id:fk' 或 'no_concat:convention'",
                        },
                        "meta": {
                            "type": "object",
                            "description": "初始 meta 数据（可选）",
                        },
                        "edges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "string", "description": "节点 ref"},
                                    "b": {"type": "string", "description": "节点 ref"},
                                },
                                "required": ["a", "b"],
                            },
                            "description": "要添加的关系边（可选）",
                        },
                    },
                    "required": ["ref"],
                },
            },
        },
        "update_meta": {
            "type": "function",
            "function": {
                "name": "update_meta",
                "description": _load_prompt("update_meta"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "Entity name, ref pattern, or full ref returned by find/meta; must match one entity",
                        },
                        "fields": {
                            "type": "object",
                            "description": "要覆盖写入的字段和值",
                        },
                    },
                    "required": ["ref", "fields"],
                },
            },
        },
        "add_edge": {
            "type": "function",
            "function": {
                "name": "add_edge",
                "description": _load_prompt("add_edge"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "edges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "string", "description": "Node ref returned by find/meta, or a unique entity name"},
                                    "b": {"type": "string", "description": "Node ref returned by find/meta, or a unique entity name"},
                                },
                                "required": ["a", "b"],
                            },
                            "description": "要添加的边列表",
                        },
                    },
                    "required": ["edges"],
                },
            },
        },
        "delete": {
            "type": "function",
            "function": {
                "name": "delete",
                "description": _load_prompt("delete"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "Entity name, ref pattern, or full ref returned by find/meta; must match one entity",
                        },
                    },
                    "required": ["ref"],
                },
            },
        },
    }


def _build_agent_schema() -> dict:
    """构建子智能体工具的 OpenAI function schema。"""
    return {
        "type": "function",
        "function": {
            "name": "agent",
            "description": _load_prompt("agent"),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "详细任务描述。子智能体看不到你的对话历史，需要提供完整背景和具体要求",
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "子智能体最大 tool call 轮次，默认 40",
                    },
                    "description": {
                        "type": "string",
                        "description": "简短任务摘要（3-5词），用于日志显示",
                    },
                },
                "required": ["task"],
            },
        },
    }


# ==================== Tool Executors ====================

def _exec_find(workspace, arguments: dict) -> str:
    from tool.find.tool import find_command
    return find_command(
        workspace,
        ref=arguments.get("ref", ""),
        query=arguments.get("query", ""),
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit"),
    )


def _exec_grep(workspace, arguments: dict) -> str:
    from tool.grep.tool import grep_command
    return grep_command(
        workspace,
        pattern=arguments["pattern"],
        ref=arguments.get("ref", ""),
        output_mode=arguments.get("output_mode", "files_with_matches"),
        file_pattern=arguments.get("file_pattern"),
        ignore_case=arguments.get("ignore_case", False),
        head_limit=arguments.get("head_limit", 250),
        offset=arguments.get("offset", 0),
    )


def _exec_read(workspace, arguments: dict) -> str:
    from tool.read.tool import read_command
    return read_command(
        workspace,
        ref=arguments.get("ref", ""),
        start_line=arguments.get("start_line", 1),
        end_line=arguments.get("end_line"),
    )


def _exec_jd(workspace, arguments: dict) -> str:
    from tool.jd.tool import jd_command
    return jd_command(
        workspace,
        ref=arguments.get("ref", ""),
        limit=arguments.get("limit", 50),
        offset=arguments.get("offset", 0),
        max_value_chars=arguments.get("max_value_chars", 120),
    )


def _exec_meta(workspace, arguments: dict) -> str:
    from tool.meta.tool import meta_command
    return meta_command(
        workspace,
        ref=arguments["ref"],
        all=arguments.get("all", False),
        property=arguments.get("property"),
    )


def _exec_bash(workspace, arguments: dict) -> str:
    from tool.bash.tool import bash_command
    return bash_command(
        command=arguments["command"],
        cwd=workspace.project_path,
        timeout_ms=arguments.get("timeout", 120) * 1000,
        workspace=workspace,
    )


def _exec_query(workspace, arguments: dict) -> str:
    from tool.query.tool import query_command
    return query_command(
        workspace,
        sql=arguments["sql"],
        ref=arguments.get("ref", ""),
        limit=arguments.get("limit", 20),
    )


def _exec_cypher(workspace, arguments: dict) -> str:
    from tool.cypher.tool import cypher_command
    return cypher_command(
        workspace,
        query=arguments["query"],
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit", 100),
        project=arguments.get("project"),
    )


def _exec_create_entity(workspace, arguments: dict) -> str:
    from tool.create_entity.tool import create_entity_command
    return create_entity_command(
        workspace,
        ref=arguments["ref"],
        meta=arguments.get("meta"),
        edges=arguments.get("edges"),
    )


def _exec_update_meta(workspace, arguments: dict) -> str:
    from tool.update_meta.tool import update_meta_command
    ref = arguments.get("ref") or arguments.get("path")
    if not ref:
        return "Error: missing required field 'ref'"
    return update_meta_command(
        workspace,
        ref=ref,
        fields=arguments.get("fields", {}),
    )


def _exec_add_edge(workspace, arguments: dict) -> str:
    from tool.add_edge.tool import add_edge_command
    return add_edge_command(workspace, edges=arguments["edges"])


def _exec_delete(workspace, arguments: dict) -> str:
    from tool.delete.tool import delete_command
    return delete_command(workspace, ref=arguments["ref"])


# ==================== Schema & Executor Tables ====================

_READONLY_SCHEMAS = None
_WRITE_SCHEMAS = None
_AGENT_SCHEMA = None


def _get_readonly_schemas() -> Dict[str, dict]:
    global _READONLY_SCHEMAS
    if _READONLY_SCHEMAS is None:
        _READONLY_SCHEMAS = _build_readonly_schemas()
    return _READONLY_SCHEMAS


def _get_write_schemas() -> Dict[str, dict]:
    global _WRITE_SCHEMAS
    if _WRITE_SCHEMAS is None:
        _WRITE_SCHEMAS = _build_write_schemas()
    return _WRITE_SCHEMAS


def _get_agent_schema() -> dict:
    global _AGENT_SCHEMA
    if _AGENT_SCHEMA is None:
        _AGENT_SCHEMA = _build_agent_schema()
    return _AGENT_SCHEMA


# (schema_dict, executor_fn) — 工具注册的原子单元
_READONLY_EXECUTORS = {
    "find": _exec_find,
    "grep": _exec_grep,
    "read": _exec_read,
    "jd": _exec_jd,
    "meta": _exec_meta,
    "bash": _exec_bash,
    "query": _exec_query,
    "cypher": _exec_cypher,
}

_WRITE_EXECUTORS = {
    "create_entity": _exec_create_entity,
    "update_meta": _exec_update_meta,
    "add_edge": _exec_add_edge,
    "delete": _exec_delete,
}


def build_registry(spec) -> ToolRegistry:
    """根据 AgentSpec 构建工具注册表。

    使用 spec.tools 作为工具列表；调用方必须显式设置。
    """
    tool_names = spec.tools

    readonly_schemas = _get_readonly_schemas()
    write_schemas = _get_write_schemas()
    writable_agent = any(name in write_schemas for name in tool_names)

    registry = ToolRegistry()

    for name in tool_names:
        if name == "agent":
            from agent.tool_use.sub_agent.tool import AgentExecutor
            registry.register("agent", _get_agent_schema(),
                              AgentExecutor(registry, writable=writable_agent))
        elif name in readonly_schemas:
            registry.register(name, readonly_schemas[name], _READONLY_EXECUTORS[name])
        elif name in write_schemas:
            registry.register(name, write_schemas[name], _WRITE_EXECUTORS[name])

    return registry
