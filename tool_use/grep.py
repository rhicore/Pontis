"""grep command - Search content across files and entities

Usage:
    # Search in physical file
    python -m tool_use.grep <project_path> <file_path> <pattern>

    # Search in database entity
    python -m tool_use.grep <project_path> <db_file> <entity> <pattern>

Format:
    [path] | [Index] | [Content]

    For databases: [path] | {pk_name}={pk_value} | {col_name}={value}

Examples:
    python -m tool_use.grep ./my_project script.py "def main"
    python -m tool_use.grep ./my_project mydb.db users.table "john"
"""
import sys
import os
import re
from typing import Optional, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext

# Entity suffixes (knowledge graph entities)
ENTITY_SUFFIXES = {'.table', '.view', '.col', '.fk', '.rel', '.overlap', '.chunk', '.summary'}


def parse_grep_args(args: list) -> Tuple[Optional[str], str, bool, int, int]:
    """Parse grep command arguments.

    Returns: (entity_name, pattern, case_insensitive, context_before, context_after)
    """
    entity_name = None
    pattern = None
    case_insensitive = False
    context_before = 0
    context_after = 0

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ['-i', '--ignore-case']:
            case_insensitive = True
            i += 1
        elif arg in ['-B', '--before-context'] and i + 1 < len(args):
            try:
                context_before = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ['-A', '--after-context'] and i + 1 < len(args):
            try:
                context_after = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ['-C', '--context'] and i + 1 < len(args):
            try:
                context_before = int(args[i + 1])
                context_after = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif not arg.startswith('-'):
            # First non-flag could be entity or pattern
            if entity_name is None and any(arg.endswith(suffix) for suffix in ENTITY_SUFFIXES):
                entity_name = arg
            elif pattern is None:
                pattern = arg
            i += 1
        else:
            i += 1

    return entity_name, pattern or "", case_insensitive, context_before, context_after


def grep_text_file(file_path: str, pattern: str, case_insensitive: bool = False,
                   context_before: int = 0, context_after: int = 0) -> List[Tuple[str, str, str]]:
    """Grep a text file, return list of (path, index, content) tuples.
    Index is line number for text files.
    """
    results = []
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Track which lines match
        match_lines = set()
        for i, line in enumerate(lines, start=1):
            if re.search(pattern, line, flags):
                match_lines.add(i)
                # Add context lines
                for ctx in range(max(1, i - context_before), i):
                    pass  # Will be handled below
                for ctx in range(i + 1, min(len(lines) + 1, i + context_after + 1)):
                    pass

        # Generate output with context
        displayed_lines = set()
        for i, line in enumerate(lines, start=1):
            is_match = i in match_lines

            if is_match:
                # Include context before
                for ctx in range(max(1, i - context_before), i):
                    if ctx not in displayed_lines:
                        displayed_lines.add(ctx)
                        ctx_line = lines[ctx - 1].rstrip()
                        results.append((os.path.basename(file_path), str(ctx), ctx_line))

                # The match itself
                if i not in displayed_lines:
                    displayed_lines.add(i)
                    line_content = line.rstrip()
                    results.append((os.path.basename(file_path), str(i), line_content))

                # Include context after
                for ctx in range(i + 1, min(len(lines) + 1, i + context_after + 1)):
                    if ctx not in displayed_lines:
                        displayed_lines.add(ctx)
                        ctx_line = lines[ctx - 1].rstrip()
                        results.append((os.path.basename(file_path), str(ctx), ctx_line))

        return results
    except Exception:
        return []


def grep_db_table(pontis_root: str, db_file: str, table_entity: str,
                  pattern: str, case_insensitive: bool = False) -> List[Tuple[str, str, str]]:
    """Grep a database table entity.
    Returns: [(path, pk_expr, col_expr), ...]
    Format: path | pk_name=pk_value | col_name=value
    """
    try:
        import sqlite3
        import yaml

        meta_path = os.path.join(pontis_root, db_file, '_meta.yml')
        if not os.path.exists(meta_path):
            return []

        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f) or {}

        source_path = meta.get('path')
        if not source_path:
            return []

        db_path = os.path.join(os.path.dirname(pontis_root), source_path)
        if not os.path.exists(db_path):
            return []

        table_name = table_entity.replace('.table', '')
        path_name = table_entity

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get column info
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()

        if not columns_info:
            conn.close()
            return []

        column_names = [col[1] for col in columns_info]
        pk_column = next((col[1] for col in columns_info if col[5] == 1), None)

        # Query all data
        cursor.execute(f'SELECT * FROM "{table_name}"')
        rows = cursor.fetchall()
        conn.close()

        results = []
        flags = re.IGNORECASE if case_insensitive else 0

        for row in rows:
            # Check each column for match
            for col_idx, val in enumerate(row):
                val_str = str(val) if val is not None else ""
                if re.search(pattern, val_str, flags):
                    # Found match in this column
                    col_name = column_names[col_idx]

                    # Build pk expression
                    if pk_column:
                        pk_idx = column_names.index(pk_column)
                        pk_val = row[pk_idx]
                        pk_expr = f"{pk_column}={pk_val}"
                    else:
                        pk_expr = f"row_idx={rows.index(row)}"

                    # Build col expression
                    col_expr = f"{col_name}={val_str}"

                    results.append((path_name, pk_expr, col_expr))
                    break  # Only report first match per row

        return results
    except Exception:
        return []


def grep_db_column(pontis_root: str, db_file: str, col_entity: str,
                   pattern: str, case_insensitive: bool = False) -> List[Tuple[str, str, str]]:
    """Grep a database column entity.
    Returns: [(path, pk_expr, col_expr), ...]
    """
    try:
        import sqlite3
        import yaml

        parts = col_entity.replace('.col', '').split('.')
        if len(parts) < 3:
            return []

        table_name = parts[0]
        column_name = parts[1]

        meta_path = os.path.join(pontis_root, db_file, '_meta.yml')
        if not os.path.exists(meta_path):
            return []

        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f) or {}

        source_path = meta.get('path')
        if not source_path:
            return []

        db_path = os.path.join(os.path.dirname(pontis_root), source_path)
        if not os.path.exists(db_path):
            return []

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get PK column
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()
        pk_column = next((col[1] for col in columns_info if col[5] == 1), None)

        # Query data
        if pk_column:
            cursor.execute(
                f'SELECT "{pk_column}", "{column_name}" FROM "{table_name}"'
            )
        else:
            cursor.execute(
                f'SELECT rowid, "{column_name}" FROM "{table_name}"'
            )
            pk_column = "rowid"

        rows = cursor.fetchall()
        conn.close()

        results = []
        flags = re.IGNORECASE if case_insensitive else 0

        for pk_val, col_val in rows:
            val_str = str(col_val) if col_val is not None else ""
            if re.search(pattern, val_str, flags):
                pk_expr = f"{pk_column}={pk_val}"
                col_expr = f"{column_name}={val_str}"
                results.append((col_entity, pk_expr, col_expr))

        return results
    except Exception:
        return []


def format_grep_results(results: List[Tuple[str, str, str]]) -> str:
    """Format grep results as: [path] | [Index] | [Content]"""
    if not results:
        return "No matches found"

    lines = []
    for path, index, content in results:
        line = f"{path} | {index} | {content}"
        lines.append(line)

    return "\n".join(lines)


def grep_command(
    project_path: str,
    file_path: str,
    args: list = None,
    current_cwd: str = ""
) -> str:
    """Search content in files and entities.

    Usage:
        grep <project> <file> <pattern> [options]           # Search physical file
        grep <project> <file> <entity> <pattern> [options]  # Search entity
    """
    if args is None:
        args = []

    pontis_root = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        # Resolve file path
        resolved_path = ctx.resolve_path(file_path)
        pontis_full_path = os.path.join(pontis_root, resolved_path)

        # Parse arguments
        entity_name, pattern, case_insensitive, context_before, context_after = parse_grep_args(args)

        if not pattern:
            return "Error: No pattern specified"

        # Check if it's a knowledge graph entity grep
        if entity_name:
            if not os.path.exists(pontis_full_path):
                return f"Error: File not found: {file_path}"

            if entity_name.endswith('.table'):
                results = grep_db_table(pontis_root, resolved_path, entity_name,
                                       pattern, case_insensitive)
            elif entity_name.endswith('.col'):
                results = grep_db_column(pontis_root, resolved_path, entity_name,
                                        pattern, case_insensitive)
            else:
                return f"Error: Unsupported entity type: {entity_name}"

            return format_grep_results(results)

        # Grep physical file
        physical_full_path = os.path.join(project_path, resolved_path)
        if not os.path.exists(physical_full_path):
            return f"Error: File not found: {file_path}"

        if os.path.isdir(physical_full_path):
            return f"Error: Cannot grep directory: {file_path}"

        results = grep_text_file(physical_full_path, pattern, case_insensitive,
                                context_before, context_after)

        return format_grep_results(results)

    except Exception as e:
        return f"Error grepping: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    project_path = sys.argv[1]
    file_path = sys.argv[2]
    args = sys.argv[3:]

    print(grep_command(project_path, file_path, args))
