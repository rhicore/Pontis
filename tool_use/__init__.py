"""
Pontis Tool Use - Agent tool implementations for physical files and entities.

Each tool is in its own subdirectory:
    glob/    - Physical file and entity glob search
    grep/    - Content search (ripgrep-based, with entity support)
    read/    - Read files and entities
    meta/    - View metadata for files and entities
    lookup/  - Value-based search for data entities
    search/  - Semantic search (placeholder)
    bash/    - Shell command execution

All tools support the path::entity pattern syntax for unified file/entity addressing.

Shared utilities in utils/:
    path_parser     - Unified path::entity pattern parser
    config          - Display type configurations
    formatters      - Shared formatting logic
    serialized_vfs  - JSON/YAML virtual navigation (reads source files directly)
"""

# Shared utilities
from tool_use.utils.path_parser import parse_path_pattern, ParsedPath
from tool_use.utils.formatters import (
    get_type_config,
    can_ls_node,
    format_info_from_template,
    format_serialized_info,
)
from tool_use.utils.config import TypeConfig
from tool_use.utils.serialized_vfs import (
    SerializedVFSEngine,
    SerializedNode,
    JsonNodeType,
)

# Tool commands
from tool_use.glob.tool import glob_command
from tool_use.grep.tool import grep_command
from tool_use.read.tool import read_command
from tool_use.meta.tool import meta_command
from tool_use.lookup.tool import lookup_command
from tool_use.search.tool import search_command
from tool_use.bash.tool import bash_command

__all__ = [
    # Utils
    'parse_path_pattern',
    'ParsedPath',
    'get_type_config',
    'can_ls_node',
    'format_info_from_template',
    'format_serialized_info',
    'TypeConfig',
    'SerializedVFSEngine',
    'SerializedNode',
    'JsonNodeType',
    # Tool commands
    'glob_command',
    'grep_command',
    'read_command',
    'meta_command',
    'lookup_command',
    'search_command',
    'bash_command',
]

# Tool registry for agent framework
TOOL_REGISTRY = {
    'glob': glob_command,
    'grep': grep_command,
    'read': read_command,
    'meta': meta_command,
    'lookup': lookup_command,
    'search': search_command,
    'bash': bash_command,
}
