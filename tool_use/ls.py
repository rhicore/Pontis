"""ls command - List physical directory contents with Pontis meta info

Usage:
    python -m tool_use.ls <project_path> [directory]

Examples:
    python -m tool_use.ls ./my_project
    python -m tool_use.ls ./my_project dev_databases/financial
"""
import sys
import os
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def get_meta_info(pontis_root: str, rel_path: str) -> dict:
    """Get meta info for a physical file from pontis."""
    meta_path = os.path.join(pontis_root, rel_path, "_meta.yml")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except:
            pass
    return {}


def format_info(meta: dict, is_dir: bool) -> str:
    """Format info field based on meta data."""
    if not meta:
        return "-"

    # Database: table_count, view_count
    if 'table_count' in meta:
        tables = meta.get('table_count', 0)
        views = meta.get('view_count', 0)
        return f"{tables} tables, {views} views"

    # Table: row_count, column_count
    if 'row_count' in meta and 'column_count' in meta:
        rows = meta['row_count']
        cols = meta['column_count']
        return f"{rows} rows, {cols} cols"

    # CSV: row_count, column_count
    if meta.get('path', '').endswith('.csv') and 'row_count' in meta:
        rows = meta['row_count']
        cols = meta.get('column_count', '-')
        return f"{rows} rows, {cols} cols"

    # JSON/YAML: structure info
    if 'structure_type' in meta:
        stype = meta['structure_type']
        if stype == 'object' and 'key_count' in meta:
            return f"object{{{meta['key_count']}}}"
        if stype == 'array' and 'array_length' in meta:
            return f"array[{meta['array_length']}]"
        return stype

    # Text files: line_count
    if 'line_count' in meta:
        return f"{meta['line_count']} lines"

    # Column: cardinality
    if 'cardinality' in meta:
        return f"distinct: {meta['cardinality']}"

    return "-"


def format_brief(meta: dict) -> str:
    """Format brief field from meta."""
    if not meta:
        return ""

    brief = meta.get('semantic_summary') or meta.get('short_summary') or ""
    if brief and len(brief) > 25:
        brief = brief[:22] + "..."
    return brief


def is_physical_file_container(full_path: str) -> bool:
    """
    Check if path is a container that should be treated as a file (not enterable).
    These are pontis virtual directories like .db, .csv that have entities inside.
    """
    if not os.path.isdir(full_path):
        return False

    # Check if directory name looks like a pontis virtual file
    name = os.path.basename(full_path)

    # Extensions that are treated as files in ls/cd
    file_extensions = ['.db', '.csv', '.tsv', '.json', '.yaml', '.yml', '.md', '.txt']
    for ext in file_extensions:
        if name.endswith(ext):
            return True

    return False


def ls_command(project_path: str, path: str = ".", current_cwd: str = "") -> str:
    """
    List physical directory contents with Pontis meta info.

    Args:
        project_path: Path to project directory (containing .pontis)
        path: Path to list (relative to project)
        current_cwd: Current working directory (for relative paths)

    Returns:
        Formatted listing of directory contents
    """
    pontis_root = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    try:
        # Build full path
        if os.path.isabs(path):
            full_path = path
        elif current_cwd:
            full_path = os.path.join(project_path, current_cwd, path)
        else:
            full_path = os.path.join(project_path, path)

        full_path = os.path.normpath(full_path)

        if not os.path.exists(full_path):
            return f"Error: Path not found: {path}"

        if not os.path.isdir(full_path):
            return f"Error: Not a directory: {path}"

        # List physical directory
        entries = []
        for entry in os.listdir(full_path):
            # Skip hidden files and pontis directory
            if entry.startswith('.') or entry == ".pontis":
                continue

            entry_full_path = os.path.join(full_path, entry)
            rel_to_pontis = os.path.relpath(entry_full_path, project_path)

            is_dir = os.path.isdir(entry_full_path)
            is_container = is_physical_file_container(entry_full_path)

            # Get meta info from pontis
            meta = get_meta_info(pontis_root, rel_to_pontis)

            entries.append({
                'name': entry,
                'is_dir': is_dir and not is_container,  # Treat containers as files
                'is_container': is_container,  # Special flag for .db, .csv etc
                'meta': meta
            })

        if not entries:
            return f"'{path}' is empty"

        # Sort: directories first, then files
        entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))

        # Format output
        lines = []
        lines.append("[Type]     | [Name]                           | [Info]               | [Brief]")
        lines.append("-" * 85)

        for entry in entries:
            # Determine type display
            if entry['is_dir']:
                type_str = "Dir"
                name = entry['name'] + "/"
            elif entry['is_container']:
                type_str = "File+"
                name = entry['name']
            else:
                type_str = "File"
                name = entry['name']

            # Get info from meta
            info = format_info(entry['meta'], entry['is_dir'])
            brief = format_brief(entry['meta'])

            # Truncate if needed
            if len(name) > 32:
                name = name[:29] + "..."
            if len(info) > 20:
                info = info[:17] + "..."
            if len(brief) > 25:
                brief = brief[:22] + "..."

            line = f"{type_str:<10} | {name:<32} | {info:<20} | {brief}"
            lines.append(line)

        return "\n".join(lines)

    except Exception as e:
        return f"Error listing directory: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tool_use.ls <project_path> [directory]")
        print("Example: python -m tool_use.ls ./my_project dev_databases/financial")
        sys.exit(1)

    project_path = sys.argv[1]
    directory = sys.argv[2] if len(sys.argv) > 2 else "."
    print(ls_command(project_path, directory))
