"""jd command - JSON/YAML structure display (single level like ls)

Usage:
    python -m tool_use.jd <project_path> <json_or_yaml_file> [internal_path] [options]

Arguments:
    project_path      Path to project directory (containing .pontis)
    json_or_yaml_file Path to JSON/YAML file (relative to project)
    internal_path     Optional: Internal path within JSON (e.g., "ROOT.DICT.database")

Options:
    -l, --limit <n>   Maximum items to display (default: 100)

Display Format:
    [HasSub] | [Name]                    | [Info]               | [Brief]

Examples:
    # Show root level
    python -m tool_use.jd ./my_project config.json

    # Show nested level
    python -m tool_use.jd ./my_project config.json ROOT.DICT.database

    # Show array items
    python -m tool_use.jd ./my_project data.json users.LIST
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def parse_jd_args(args: list) -> tuple:
    """Parse jd command arguments.

    Returns: (internal_path, limit)
    """
    internal_path = None
    limit = 100

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ['-l', '--limit'] and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif not arg.startswith('-') and internal_path is None:
            # First non-flag arg is internal path
            internal_path = arg
            i += 1
        else:
            i += 1

    return internal_path, limit


def get_value_type(value) -> str:
    """Get the type of a value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return f"string({len(value)})" if len(value) > 0 else "string"
    if isinstance(value, list):
        return f"array[{len(value)}]"
    if isinstance(value, dict):
        return f"dict{{{len(value)}}}"
    return type(value).__name__


def get_info(value) -> str:
    """Get info about a value for the Info column."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Show preview for strings
        preview = value.replace('\n', ' ')[:25]
        if len(value) > 25:
            preview += "..."
        return preview
    if isinstance(value, list):
        if len(value) == 0:
            return "empty"
        # Show type of first element
        first_type = get_value_type(value[0])
        return f"items: {first_type}"
    if isinstance(value, dict):
        if len(value) == 0:
            return "empty"
        keys = list(value.keys())[:3]
        return f"keys: {', '.join(keys)}" + ("..." if len(value) > 3 else "")
    return "-"


def get_brief(value) -> str:
    """Get brief description for the Brief column."""
    if isinstance(value, dict):
        return f"{len(value)} properties"
    if isinstance(value, list):
        return f"{len(value)} items"
    if isinstance(value, str) and len(value) > 50:
        return value[:47] + "..."
    return ""


def parse_path_parts(path: str) -> list:
    """Parse path string into parts, handling both dot notation and bracket notation.

    Examples:
        "ROOT.DICT.key1.LIST.0" -> ["ROOT", "DICT", "key1", "LIST", "0"]
        "[0]" -> ["0"]
        "ROOT.DICT.[0].key" -> ["ROOT", "DICT", "0", "key"]
    """
    import re
    # Remove brackets and split by dots
    # Replace [n] with .n. for easier parsing
    normalized = path.replace('/', '.')
    normalized = re.sub(r'\[(\d+)\]', r'.\1.', normalized)
    # Remove empty parts and type markers
    parts = [p for p in normalized.split('.') if p and p not in ["DICT", "LIST", "ARRAY", "OBJECT"]]
    return parts


def navigate_to_path(data, path: str):
    """Navigate to a specific path in the data structure.

    Path format: "ROOT.DICT.key1.LIST.0" or "[0]" or "key.subkey"
    """
    if not path:
        return data, ""

    parts = parse_path_parts(path)
    current = data

    for part in parts:
        if part == "ROOT":
            continue

        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return None, f"Key '{part}' not found"
        elif isinstance(current, list):
            try:
                idx = int(part)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None, f"Index {idx} out of range (0-{len(current)-1})"
            except ValueError:
                return None, f"Invalid array index: {part}"
        else:
            return None, f"Cannot navigate into {type(current).__name__}"

    return current, ""


def get_root_type(data) -> str:
    """Get the type of the root data."""
    if isinstance(data, dict):
        return "dict"
    if isinstance(data, list):
        return "array"
    return "scalar"


def format_node_name(key, parent_type: str = "dict") -> str:
    """Format the display name for a node."""
    if parent_type == "array":
        return f"[{key}]"
    return str(key)


def format_jd_output(items: list, parent_path: str = "") -> str:
    """Format items for jd output using ls-style format.

    Format: [HasSub] | [Name] | [Info] | [Brief]
    """
    if not items:
        return "(empty)"

    lines = []
    lines.append("[HasSub] | [Name]                           | [Info]               | [Brief]")
    lines.append("-" * 85)

    for item in items:
        has_sub = item.get('has_children', False)
        name = item.get('name', '')
        info = item.get('info', '')
        brief = item.get('brief', '')

        has_sub_str = "[+]" if has_sub else "[ ]"

        # Truncate if needed
        if len(name) > 30:
            name = name[:27] + "..."
        if len(info) > 20:
            info = info[:17] + "..."
        if len(brief) > 20:
            brief = brief[:17] + "..."

        line = f"{has_sub_str:<8} | {name:<30} | {info:<20} | {brief}"
        lines.append(line)

    return "\n".join(lines)


def list_current_level(data, parent_path: str = "", limit: int = 100) -> list:
    """List current level of the data structure (single level only).

    Returns list of item dicts with keys: name, has_children, info, brief
    """
    items = []

    if isinstance(data, dict):
        for key, value in data.items():
            has_children = isinstance(value, (dict, list)) and len(value) > 0
            item = {
                'name': str(key),
                'has_children': has_children,
                'info': get_value_type(value),
                'brief': get_info(value),
            }
            items.append(item)

    elif isinstance(data, list):
        for i, value in enumerate(data):
            has_children = isinstance(value, (dict, list)) and len(value) > 0
            item = {
                'name': f"[{i}]",
                'has_children': has_children,
                'info': get_value_type(value),
                'brief': get_info(value),
            }
            items.append(item)

    else:
        # Scalar value - nothing to list
        item = {
            'name': "(scalar value)",
            'has_children': False,
            'info': get_value_type(data),
            'brief': get_info(data),
        }
        items.append(item)

    return items[:limit]


def jd_command(
    project_path: str,
    file_path: str,
    args: list,
    current_cwd: str = ""
) -> str:
    """Display JSON/YAML file structure (single level like ls)."""
    pontis_root = os.path.join(project_path, ".pontis")

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        # Resolve file path (relative to project, not pontis)
        # JSON/YAML files are physical files in the project
        if os.path.isabs(file_path):
            full_path = file_path
        else:
            # First try relative to project path
            full_path = os.path.join(project_path, file_path)
            if not os.path.exists(full_path):
                # Then try relative to cwd
                full_path = os.path.join(project_path, current_cwd, file_path)

        if not os.path.exists(full_path):
            return f"Error: File not found: {file_path}"

        # Parse arguments
        internal_path, limit = parse_jd_args(args)

        # Load file
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.json':
            import json
            with open(full_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        elif ext in ['.yaml', '.yml']:
            import yaml
            with open(full_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        else:
            return f"Error: Unsupported file type: {ext}"

        # Navigate to internal path if specified
        if internal_path:
            data, error = navigate_to_path(data, internal_path)
            if error:
                return f"Error: {error}"
            if data is None:
                return f"Error: Path not found: {internal_path}"
            display_path = f"{file_path}/{internal_path}"
        else:
            display_path = file_path

        # List current level
        items = list_current_level(data, internal_path or "", limit)

        # Build output
        lines = []
        lines.append(f"Structure of: {display_path}")
        lines.append("")
        lines.append(format_jd_output(items, internal_path or ""))

        # Show navigation hint
        if items:
            has_sub = any(item.get('has_children') for item in items)
            if has_sub:
                lines.append("")
                lines.append("Use 'jd <file> <path>' to navigate deeper (e.g., jd config.json ROOT.DICT.database)")

        return "\n".join(lines)

    except Exception as e:
        return f"Error displaying structure: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    project_path = sys.argv[1]
    file_path = sys.argv[2]
    args = sys.argv[3:]

    print(jd_command(project_path, file_path, args))
