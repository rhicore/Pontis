"""Tool definitions and execution dispatcher for the agent.

Uses ToolRegistry pattern: tools are registered with (schema, executor) pairs,
allowing different agent modes to have different tool sets.
"""
import importlib
from typing import Callable, Dict, List, Tuple


# tool_name → directory name in agent/tool_use/ (when they differ)
_TOOL_DIR_MAP = {
    "agent": "sub_agent",
    "bash": "SH_bash",
    "grep": "FS_grep",
    "query": "DB_query",
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
    """工具注册表，支持组合不同模式下的工具集。"""

    def __init__(self):
        self._tools: Dict[str, Tuple[dict, Callable]] = {}

    def register(self, name: str, schema: dict, executor: Callable):
        """注册工具: name → (OpenAI function schema, executor(store, arguments) -> str)"""
        self._tools[name] = (schema, executor)

    def get_definitions(self) -> List[dict]:
        """返回 OpenAI tool calling schema 列表。"""
        return [schema for schema, _ in self._tools.values()]

    def execute(self, name: str, arguments: dict, store, workspace=None) -> str:
        """执行工具调用。传入 workspace 供新式工具使用。"""
        if name not in self._tools:
            return f"Unknown tool: {name}"
        _, executor = self._tools[name]
        try:
            return executor(store, arguments, workspace=workspace)
        except Exception as e:
            return f"Tool error ({name}): {type(e).__name__}: {e}"

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())


# ==================== Tool Schemas ====================

def _build_readonly_schemas() -> Dict[str, dict]:
    """构建只读工具的 OpenAI function schema。"""
    return {
        "glob": {
            "type": "function",
            "function": {
                "name": "glob",
                "description": _load_prompt("glob"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
                            "type": "string",
                            "description": "URN glob pattern, e.g. '*.db' for files, '*.*.*:col' for columns, supports / hop, ** varlen, :: project",
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
                            "description": "实体名称，如 'users' (表) 或 'event.db'",
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
                            "description": "Show specific property or list of properties, e.g. 'brief' or ['brief','detail']",
                        },
                    },
                    "required": ["ref"],
                },
            },
        },
        "search": {
            "type": "function",
            "function": {
                "name": "search",
                "description": _load_prompt("search"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {
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
                    "required": ["ref", "query"],
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
                        "file": {
                            "type": "string",
                            "description": "Database file path relative to project root, e.g. 'data.sqlite'",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max rows to return, default 100",
                        },
                    },
                    "required": ["sql", "file"],
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
                            "description": "实体引用，格式 [project::]name[:tag1[:tag2]]，如 'account.id->district.id:fk' 或 'no_concat:convention'",
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
                            "description": "实体引用：名称或 glob 模式（必须唯一匹配）",
                        },
                        "fields": {
                            "type": "object",
                            "description": "要更新的字段，允许 brief 和 detail",
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
                                    "a": {"type": "string", "description": "节点 ref（名称或 glob 模式）"},
                                    "b": {"type": "string", "description": "节点 ref（名称或 glob 模式）"},
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
                            "description": "实体引用：名称或 glob 模式（必须唯一匹配）",
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

def _exec_glob(store, arguments: dict, *, workspace=None) -> str:
    from tool.glob.tool import glob_command
    return glob_command(
        workspace or store, arguments["ref"],
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit"),
    )


def _exec_grep(store, arguments: dict, *, workspace=None) -> str:
    from tool.FS_grep.tool import grep_command
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


def _exec_meta(store, arguments: dict, *, workspace=None) -> str:
    from tool.meta.tool import meta_command
    return meta_command(
        workspace or store,
        ref=arguments["ref"],
        all=arguments.get("all", False),
        property=arguments.get("property"),
    )


def _exec_search(store, arguments: dict, *, workspace=None) -> str:
    from tool.search.tool import search_command
    return search_command(
        workspace or store,
        ref=arguments["ref"],
        query=arguments["query"],
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit"),
    )


def _exec_bash(store, arguments: dict, *, workspace=None) -> str:
    from tool.SH_bash.tool import bash_command
    return bash_command(
        command=arguments["command"],
        cwd=store.project_path,
        timeout_ms=arguments.get("timeout", 120) * 1000,
    )


def _exec_query(store, arguments: dict, *, workspace=None) -> str:
    from tool.DB_query.tool import query_command
    return query_command(
        store,
        sql=arguments["sql"],
        file=arguments["file"],
        limit=arguments.get("limit", 100),
    )


def _exec_cypher(store, arguments: dict, *, workspace=None) -> str:
    from tool.cypher.tool import cypher_command
    return cypher_command(
        workspace or store,
        query=arguments["query"],
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit", 100),
    )


def _exec_create_entity(store, arguments: dict, *, workspace=None) -> str:
    from tool.create_entity.tool import create_entity_command
    return create_entity_command(
        workspace or store,
        ref=arguments["ref"],
        meta=arguments.get("meta"),
        edges=arguments.get("edges"),
    )


def _exec_update_meta(store, arguments: dict, *, workspace=None) -> str:
    from tool.update_meta.tool import update_meta_command
    return update_meta_command(
        workspace or store,
        ref=arguments["ref"],
        fields=arguments["fields"],
    )


def _exec_add_edge(store, arguments: dict, *, workspace=None) -> str:
    from tool.add_edge.tool import add_edge_command
    return add_edge_command(workspace or store, edges=arguments["edges"])


def _exec_delete(store, arguments: dict, *, workspace=None) -> str:
    from tool.delete.tool import delete_command
    return delete_command(workspace or store, ref=arguments["ref"])


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
    "glob": _exec_glob,
    "grep": _exec_grep,
    "meta": _exec_meta,
    "search": _exec_search,
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

    使用 spec.tools（由 resolve_mode 填充）作为工具列表。
    """
    tool_names = spec.tools
    mode = spec.mode

    readonly_schemas = _get_readonly_schemas()
    write_schemas = _get_write_schemas()

    registry = ToolRegistry()

    for name in tool_names:
        if name == "agent":
            from agent.tool_use.sub_agent.tool import AgentExecutor
            sub_mode = "writer" if mode in ("writer", "sub_agent") else "readonly"
            registry.register("agent", _get_agent_schema(),
                              AgentExecutor(registry, mode=sub_mode))
        elif name in readonly_schemas:
            registry.register(name, readonly_schemas[name], _READONLY_EXECUTORS[name])
        elif name in write_schemas:
            registry.register(name, write_schemas[name], _WRITE_EXECUTORS[name])

    return registry


# ==================== 向后兼容封装 ====================

def build_readonly_registry() -> ToolRegistry:
    """向后兼容：构建只读模式工具集。"""
    from agent.config import AgentSpec, resolve_mode
    spec = AgentSpec(mode="readonly")
    resolve_mode(spec)
    return build_registry(spec)


def build_writer_registry() -> ToolRegistry:
    """向后兼容：构建写入模式工具集。"""
    from agent.config import AgentSpec, resolve_mode
    spec = AgentSpec(mode="writer")
    resolve_mode(spec)
    return build_registry(spec)


# ==================== 向后兼容 ====================

def execute_tool(name: str, arguments: dict, store, workspace=None) -> str:
    """执行工具（向后兼容）。"""
    from agent.config import AgentSpec, resolve_mode
    spec = AgentSpec()
    resolve_mode(spec)
    registry = build_registry(spec)
    return registry.execute(name, arguments, store, workspace=workspace)
