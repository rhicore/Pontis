"""Tool definitions and execution dispatcher for the agent.

Uses ToolRegistry pattern: tools are registered with (schema, executor) pairs,
allowing different agent modes to have different tool sets.
"""
import importlib
from typing import Callable, Dict, List, Any, Tuple


# tool_name → tool_use directory mapping
_TOOL_DIR_MAP = {
    "agent": "sub_agent",
}


def _load_prompt(tool_name: str) -> str:
    """Load tool prompt from tool_use/<tool>/prompt.py."""
    try:
        dir_name = _TOOL_DIR_MAP.get(tool_name, tool_name)
        mod = importlib.import_module(f"tool_use.{dir_name}.prompt")
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

    def execute(self, name: str, arguments: dict, store) -> str:
        """执行工具调用。"""
        if name not in self._tools:
            return f"Unknown tool: {name}"
        _, executor = self._tools[name]
        try:
            return executor(store, arguments)
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
                        "path_pattern": {
                            "type": "string",
                            "description": "Glob pattern with optional :: segments for edge traversal, e.g. '*.db::*.table::*.*.*.col'",
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
        "read": {
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
        "meta": {
            "type": "function",
            "function": {
                "name": "meta",
                "description": _load_prompt("meta"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Ref string: file path, path::entity, or ent_id, e.g. 'event.db::users.table'",
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
                    "required": ["path"],
                },
            },
        },
        "lookup": {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": _load_prompt("lookup"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_pattern": {
                            "type": "string",
                            "description": "Glob pattern for files (via Store graph), e.g. '*.db', '*.json'",
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
        "search": {
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
        "find_path": {
            "type": "function",
            "function": {
                "name": "find_path",
                "description": _load_prompt("find_path"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from_ref": {
                            "type": "string",
                            "description": "起始实体 ref，如 'db::TableA.table'",
                        },
                        "to_ref": {
                            "type": "string",
                            "description": "目标实体 ref，如 'db::TableB.table'",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "最大搜索深度（表跳数），默认 4",
                        },
                    },
                    "required": ["from_ref", "to_ref"],
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
                            "description": "实体引用，格式 path::entity_name，如 'event.db::user_event_join.view'",
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
                                    "required_by": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "依赖方，值为 [\"a\"] 或 [\"b\"]",
                                    },
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
                            "description": "节点引用：文件路径、path::entity、或 ent_id",
                        },
                        "fields": {
                            "type": "object",
                            "description": "要更新的字段键值对，如 {\"brief\": \"...\", \"detail\": \"...\"}",
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
                                    "a": {"type": "string", "description": "节点 ref"},
                                    "b": {"type": "string", "description": "节点 ref"},
                                    "required_by": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "依赖方列表，值为 [\"a\"] 或 [\"b\"]",
                                    },
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
                            "description": "要删除的节点 ref，如 'event.db' 或 'event.db::users.table'",
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

def _exec_glob(store, arguments: dict) -> str:
    from tool_use.glob.tool import glob_command
    return glob_command(
        store, arguments["path_pattern"],
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit"),
    )


def _exec_grep(store, arguments: dict) -> str:
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


def _exec_read(store, arguments: dict) -> str:
    from tool_use.read.tool import read_command
    kwargs = {"store": store, "file_path": arguments["file_path"]}
    if "offset" in arguments:
        kwargs["offset"] = arguments["offset"]
    if "limit" in arguments:
        kwargs["limit"] = arguments["limit"]
    return read_command(**kwargs)


def _exec_meta(store, arguments: dict) -> str:
    from tool_use.meta.tool import meta_command
    return meta_command(
        store,
        path=arguments["path"],
        all=arguments.get("all", False),
        property=arguments.get("property"),
    )


def _exec_lookup(store, arguments: dict) -> str:
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


def _exec_search(store, arguments: dict) -> str:
    from tool_use.search.tool import search_command
    return search_command(
        store,
        path_pattern=arguments["path_pattern"],
        query=arguments["query"],
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit"),
    )


def _exec_bash(store, arguments: dict) -> str:
    from tool_use.bash.tool import bash_command
    return bash_command(
        command=arguments["command"],
        cwd=store.project_path,
        timeout_ms=arguments.get("timeout", 120) * 1000,
    )


def _exec_query(store, arguments: dict) -> str:
    from tool_use.query.tool import query_command
    return query_command(
        store,
        sql=arguments["sql"],
        file=arguments["file"],
        limit=arguments.get("limit", 100),
    )


def _exec_find_path(store, arguments: dict) -> str:
    from tool_use.find_path.tool import find_path_command
    return find_path_command(
        store,
        from_ref=arguments["from_ref"],
        to_ref=arguments["to_ref"],
        max_depth=arguments.get("max_depth", 4),
    )


def _exec_create_entity(store, arguments: dict) -> str:
    from tool_use.create_entity.tool import create_entity_command
    return create_entity_command(
        store,
        ref=arguments["ref"],
        meta=arguments.get("meta"),
        edges=arguments.get("edges"),
    )


def _exec_update_meta(store, arguments: dict) -> str:
    from tool_use.update_meta.tool import update_meta_command
    return update_meta_command(
        store,
        ref=arguments["ref"],
        fields=arguments["fields"],
    )


def _exec_add_edge(store, arguments: dict) -> str:
    from tool_use.add_edge.tool import add_edge_command
    return add_edge_command(store, edges=arguments["edges"])


def _exec_delete(store, arguments: dict) -> str:
    from tool_use.delete.tool import delete_command
    return delete_command(store, ref=arguments["ref"])


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
    "read": _exec_read,
    "meta": _exec_meta,
    "lookup": _exec_lookup,
    "search": _exec_search,
    "bash": _exec_bash,
    "query": _exec_query,
    "find_path": _exec_find_path,
}

_WRITE_EXECUTORS = {
    "create_entity": _exec_create_entity,
    "update_meta": _exec_update_meta,
    "add_edge": _exec_add_edge,
    "delete": _exec_delete,
}


# ──────────────────────────────────────────────────────────
#  模式 → 默认工具集
# ──────────────────────────────────────────────────────────
# 新增模式只需在此加一行。

_MODE_TOOLS = {
    "readonly":  ["glob", "grep", "read", "meta", "lookup", "search",
                  "bash", "query", "find_path", "agent"],
    "writer":    ["glob", "grep", "read", "meta", "lookup", "search",
                  "bash", "query", "find_path", "agent",
                  "create_entity", "update_meta", "add_edge", "delete"],
    "sub_agent": ["glob", "grep", "read", "meta", "lookup", "search",
                  "bash", "query", "find_path",
                  "create_entity", "update_meta", "add_edge", "delete"],
    "benchmark": ["glob", "grep", "read", "meta", "lookup", "search",
                  "bash", "query", "find_path"],
}


def build_registry(spec) -> ToolRegistry:
    """根据 AgentSpec 构建工具注册表。

    流程：从 _MODE_TOOLS 取模式默认集合 → 排除 disabled_tools → 逐个注册。
    特殊处理：agent 工具需要根据 mode 创建 AgentExecutor。
    """
    mode = spec.mode
    base_tools = list(_MODE_TOOLS.get(mode, _MODE_TOOLS["readonly"]))
    disabled = set(spec.disabled_tools)
    enabled = [t for t in base_tools if t not in disabled]

    readonly_schemas = _get_readonly_schemas()
    write_schemas = _get_write_schemas()

    registry = ToolRegistry()

    for name in enabled:
        if name == "agent":
            # agent 工具特殊处理：executor 需要包装 registry 和 mode
            from tool_use.sub_agent.tool import AgentExecutor
            sub_mode = "writer" if mode in ("writer", "sub_agent") else "readonly"
            registry.register("agent", _get_agent_schema(),
                              AgentExecutor(registry, mode=sub_mode))
        elif name in readonly_schemas:
            registry.register(name, readonly_schemas[name], _READONLY_EXECUTORS[name])
        elif name in write_schemas:
            registry.register(name, write_schemas[name], _WRITE_EXECUTORS[name])

    # debug 模式注入 log 工具
    if spec.debug:
        registry.register("log", _build_log_schema(), _exec_log)

    return registry


# ==================== Debug Tool ====================

def _build_log_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "log",
            "description": _load_prompt("log"),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["tool_result", "tool_missing", "tool_ux", "prompt_unclear"],
                        "description": "问题类别",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "具体问题描述（中文，包含工具名、参数、期望等细节）",
                    },
                },
                "required": ["category", "feedback"],
            },
        },
    }


def _exec_log(store, arguments: dict) -> str:
    from tool_use.log.tool import log_command
    return log_command(
        category=arguments["category"],
        feedback=arguments["feedback"],
    )


def enable_debug(registry: ToolRegistry) -> None:
    """向已有 registry 注入 log 工具（调试模式）。"""
    registry.register("log", _build_log_schema(), _exec_log)


# ==================== 向后兼容封装 ====================

def build_readonly_registry() -> ToolRegistry:
    """向后兼容：构建只读模式工具集。"""
    from agent.agent import AgentSpec
    return build_registry(AgentSpec(mode="readonly"))


def build_writer_registry() -> ToolRegistry:
    """向后兼容：构建写入模式工具集。"""
    from agent.agent import AgentSpec
    return build_registry(AgentSpec(mode="writer"))


# ==================== 向后兼容 ====================

def execute_tool(name: str, arguments: dict, store) -> str:
    """执行工具（向后兼容）。"""
    from agent.agent import AgentSpec
    registry = build_registry(AgentSpec())
    return registry.execute(name, arguments, store)
