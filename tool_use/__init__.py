"""
Pontis Tool Use - Knowledge Graph Commands for Physical Files

This package provides commands to interact with the knowledge graph
entities attached to physical files in the Pontis VFS.

Commands:
    glob   - Search knowledge graph entities under a physical file
    meta   - Read/Write metadata for physical files and entities
    read   - Read content from physical files and entities
    jd     - Display JSON/YAML structure

Usage:
    from tool_use import glob, meta, read, jd

    # Search entities
    result = glob.glob_command("./my_project", "mydb.db", "*.table")

    # Get metadata
    result = meta.meta_command("./my_project", "mydb.db", [])
    result = meta.meta_command("./my_project", "mydb.db", ["users.table", "-a"])

    # Read content
    result = read.read_command("./my_project", "mydb.db", ["users.table"])

    # Display JSON structure
    result = jd.jd_command("./my_project", "config.json", [])
"""

# Utils
from tool_use.utils.context import ToolContext
from tool_use.utils.vfs import PontisVFS
from tool_use.utils.serialized_vfs import (
    SerializedVFSEngine,
    SerializedNode,
    JsonNodeType,
    is_serialized_file,
)
from tool_use.utils.config import (
    LS_TYPE_CONFIG as LS_CONFIG,
    TypeConfig,
)
from tool_use.utils.formatters import (
    get_type_config,
    can_ls_node,
    format_info_from_template,
    format_serialized_info,
)

# Commands
from tool_use.ls import ls_command
from tool_use.cd import cd_command
from tool_use.glob import glob_command
from tool_use.grep import grep_command
from tool_use.meta import meta_command
from tool_use.read import read_command
from tool_use.jd import jd_command

# Aliases for backward compatibility
pglob_command = glob_command
pmeta_command = meta_command
pread_command = read_command

__all__ = [
    # Utils
    'ToolContext',
    'PontisVFS',
    'SerializedVFSEngine',
    'SerializedNode',
    'JsonNodeType',
    'is_serialized_file',
    'LS_CONFIG',
    'TypeConfig',
    'get_type_config',
    'can_ls_node',
    'format_info_from_template',
    'format_serialized_info',
    # Commands
    'ls_command',
    'cd_command',
    'glob_command',
    'grep_command',
    'meta_command',
    'read_command',
    'jd_command',
]
