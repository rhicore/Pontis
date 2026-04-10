"""
Pontis Tool Use - Agent tool implementations.

Each tool is in its own subdirectory:
    glob/    - File and entity glob search
    grep/    - Content search (ripgrep-based)
    read/    - Read files and entities
    meta/    - View metadata for files and entities
    lookup/  - Value-based search for data entities
    search/  - Semantic search
    bash/    - Shell command execution

Shared utilities in utils/:
    path_parser     - path::entity pattern parser
    config          - Display type configurations
    formatters      - Formatting logic
"""

# Tool commands
from tool_use.glob.tool import glob_command
from tool_use.grep.tool import grep_command
from tool_use.read.tool import read_command
from tool_use.meta.tool import meta_command
from tool_use.lookup.tool import lookup_command
from tool_use.search.tool import search_command
from tool_use.bash.tool import bash_command

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
