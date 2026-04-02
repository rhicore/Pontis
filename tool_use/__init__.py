"""Tool Use module for LLM agents to interact with .pontis metadata

This module provides tools that LLM agents can use to explore and query
the virtual file system created by the Pontis extractor.
"""
from .vfs import PontisVFS
from .tools import ls, meta, search, find
from .prompts import SYSTEM_PROMPT, get_tool_descriptions

__all__ = [
    "PontisVFS",
    "ls",
    "meta",
    "search",
    "find",
    "SYSTEM_PROMPT",
    "get_tool_descriptions",
]
