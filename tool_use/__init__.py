"""
Pontis Tool Use - Knowledge Graph Commands for Physical Files

This package provides commands to interact with the knowledge graph
entities attached to physical files in the Pontis VFS.

Commands:
    pglob  - Search knowledge graph entities under a physical file
    pmeta  - Read/Write metadata for physical files and entities
    pread  - Read content from physical files and entities
    jd     - Display JSON/YAML structure

Usage:
    from tool_use import pglob, pmeta, pread, jd

    # Search entities
    result = pglob.pglob_command("./my_project", "mydb.db", "*.table")

    # Get metadata
    result = pmeta.pmeta_command("./my_project", "mydb.db", [])
    result = pmeta.pmeta_command("./my_project", "mydb.db", ["users.table", "-a"])

    # Read content
    result = pread.pread_command("./my_project", "mydb.db", ["users.table"])

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
from tool_use.pglob import pglob_command
from tool_use.pmeta import pmeta_command
from tool_use.pread import pread_command
from tool_use.jd import jd_command

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
    'pglob_command',
    'pmeta_command',
    'pread_command',
    'jd_command',
]
