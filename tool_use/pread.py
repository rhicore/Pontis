"""pread command - Read content from physical files and knowledge graph entities

Usage:
    python -m tool_use.pread <project_path> <physical_file> [entity_name] [options]

Options:
    -l, --limit <n>      Limit output to n lines/rows (default: 50)
    -o, --offset <n>     Start from offset (default: 0)

Supported Read Operations:
    # Physical files
    python -m tool_use.pread ./my_project data.json
    python -m tool_use.pread ./my_project script.py -l 100

    # Database entities
    python -m tool_use.pread ./my_project mydb.db users.table       # Read table as CSV
    python -m tool_use.pread ./my_project mydb.db users.id.INT.col  # Read column values

    # CSV files
    python -m tool_use.pread ./my_project data.csv                  # Read CSV content
    python -m tool_use.pread ./my_project data.csv name.TEXT.col    # Read single column

    # JSON/YAML entities (serialized files)
    python -m tool_use.pread ./my_project config.json ROOT.DICT.database

Examples:
    python -m tool_use.pread ./my_project dev_databases/financial/financial.db account.table
    python -m tool_use.pread ./my_project dev_databases/financial/financial.db account.account_id.INT.col
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def parse_read_args(args: list) -> tuple:
    """Parse pread command arguments.

    Returns: (entity_name, limit, offset)
    """
    entity_name = None
    limit = 50
    offset = 0

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ['-l', '--limit'] and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ['-o', '--offset'] and i + 1 < len(args):
            try:
                offset = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif not arg.startswith('-'):
            # This is the entity name
            if entity_name is None:
                entity_name = arg
            i += 1
        else:
            i += 1

    return entity_name, limit, offset


def is_entity(name: str) -> bool:
    """Check if a name looks like a knowledge graph entity."""
    if not name:
        return False
    # Entity patterns: *.table, *.col, *.view, *.fk, etc.
    entity_suffixes = ['.table', '.view', '.col', '.fk', '.rel', '.overlap', '.chunk', '.flow']
    return any(name.endswith(suffix) for suffix in entity_suffixes)


def read_physical_file(file_path: str, limit: int = 50, offset: int = 0) -> str:
    """Read a physical file (text/code files)."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Apply offset and limit
        start = offset
        end = min(offset + limit, len(lines))

        if start >= len(lines):
            return "(offset beyond file length)"

        result_lines = lines[start:end]

        # Add line numbers
        output = []
        for i, line in enumerate(result_lines, start=start + 1):
            output.append(f"{i:4d} | {line.rstrip()}")

        # Show truncation info
        if end < len(lines):
            remaining = len(lines) - end
            output.append(f"\n... ({remaining} more lines, total {len(lines)})")

        return "\n".join(output)

    except Exception as e:
        return f"Error reading file: {e}"


def read_table(pontis_root: str, physical_file: str, table_name: str, limit: int = 50, offset: int = 0) -> str:
    """Read a database table as formatted output (sorted by primary key)."""
    import sqlite3
    import csv
    from io import StringIO

    try:
        # Find the physical database file
        ctx = ToolContext(pontis_root)
        db_rel_path = physical_file.replace('.db', '')  # Remove .db suffix for meta lookup

        # Try to find the actual database file path from meta
        meta_path = os.path.join(pontis_root, physical_file, '_meta.yml')
        actual_db_path = None
        if os.path.exists(meta_path):
            import yaml
            with open(meta_path, 'r') as f:
                meta = yaml.safe_load(f) or {}
            source_path = meta.get('path')
            if source_path:
                actual_db_path = os.path.join(os.path.dirname(pontis_root), source_path)

        if not actual_db_path or not os.path.exists(actual_db_path):
            return f"Error: Database file not found for {physical_file}"

        # Get table name without suffix
        table = table_name.replace('.table', '')

        # Connect and read
        conn = sqlite3.connect(actual_db_path)
        cursor = conn.cursor()

        # Get primary key info
        cursor.execute(f'PRAGMA table_info("{table}")')
        columns_info = cursor.fetchall()
        pk_column = None
        for col in columns_info:
            if col[5] == 1:  # pk flag
                pk_column = col[1]
                break

        # Build query with ORDER BY primary key
        if pk_column:
            cursor.execute(f'SELECT * FROM "{table}" ORDER BY "{pk_column}" LIMIT ? OFFSET ?', (limit, offset))
        else:
            cursor.execute(f'SELECT * FROM "{table}" LIMIT ? OFFSET ?', (limit, offset))

        rows = cursor.fetchall()

        # Get column names
        column_names = [col[1] for col in columns_info]

        conn.close()

        if not rows:
            return "(no data or offset beyond table length)"

        # Format as table
        output = []
        output.append(" | ".join(column_names))
        output.append("-" * (len(" | ".join(column_names)) + 20))

        for row in rows:
            formatted_row = []
            for val in row:
                if val is None:
                    formatted_row.append("NULL")
                else:
                    formatted_row.append(str(val)[:30])  # Truncate long values
            output.append(" | ".join(formatted_row))

        # Show count info
        output.append(f"\n(showing {len(rows)} rows from offset {offset})")

        return "\n".join(output)

    except Exception as e:
        return f"Error reading table: {e}"


def read_column(pontis_root: str, physical_file: str, col_name: str, limit: int = 50, offset: int = 0) -> str:
    """Read a single column from a database table."""
    import sqlite3

    try:
        # Parse column entity name: table.col_name.TYPE.col
        parts = col_name.replace('.col', '').split('.')
        if len(parts) < 3:
            return f"Error: Invalid column entity name: {col_name}"

        table = parts[0]
        column = parts[1]

        # Find database file
        meta_path = os.path.join(pontis_root, physical_file, '_meta.yml')
        actual_db_path = None
        if os.path.exists(meta_path):
            import yaml
            with open(meta_path, 'r') as f:
                meta = yaml.safe_load(f) or {}
            source_path = meta.get('path')
            if source_path:
                actual_db_path = os.path.join(os.path.dirname(pontis_root), source_path)

        if not actual_db_path or not os.path.exists(actual_db_path):
            return f"Error: Database file not found"

        conn = sqlite3.connect(actual_db_path)
        cursor = conn.cursor()

        # Read column values
        cursor.execute(f'SELECT "{column}" FROM "{table}" LIMIT ? OFFSET ?', (limit, offset))
        rows = cursor.fetchall()

        conn.close()

        if not rows:
            return "(no data)"

        output = []
        output.append(f"Column: {table}.{column}")
        output.append("-" * 40)

        for i, (val,) in enumerate(rows, start=offset + 1):
            if val is None:
                output.append(f"{i:4d} | NULL")
            else:
                output.append(f"{i:4d} | {val}")

        output.append(f"\n(showing {len(rows)} values from offset {offset})")

        return "\n".join(output)

    except Exception as e:
        return f"Error reading column: {e}"


def read_csv(file_path: str, limit: int = 50, offset: int = 0) -> str:
    """Read a CSV file."""
    import csv

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.reader(f)
            rows = list(reader)

        if offset >= len(rows):
            return "(offset beyond file length)"

        # Header + data rows
        end = min(offset + limit, len(rows))
        selected_rows = rows[offset:end]

        output = []
        for i, row in enumerate(selected_rows, start=offset):
            prefix = "H" if i == 0 else f"{i:3d}"
            formatted = " | ".join(str(cell)[:25] for cell in row)
            output.append(f"{prefix} | {formatted}")

        if end < len(rows):
            remaining = len(rows) - end
            output.append(f"\n... ({remaining} more rows, total {len(rows)})")

        return "\n".join(output)

    except Exception as e:
        return f"Error reading CSV: {e}"


def read_serialized_entity(pontis_root: str, physical_file: str, entity_path: str, limit: int = 50, offset: int = 0) -> str:
    """Read an entity from a serialized file (JSON/YAML)."""
    from tool_use.utils.serialized_vfs import SerializedVFSEngine

    try:
        # Build the full virtual path
        full_path = os.path.join(pontis_root, physical_file, entity_path)

        # Check if it's a file with _raw
        raw_path = os.path.join(full_path, '_raw')
        if os.path.exists(raw_path):
            with open(raw_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Limit content
            lines = content.split('\n')
            start = offset
            end = min(offset + limit, len(lines))
            return '\n'.join(lines[start:end])

        # Otherwise use serialized VFS
        file_path = os.path.join(pontis_root, physical_file, '_raw')
        if not os.path.exists(file_path):
            return f"Error: Serialized file not found"

        handler = SerializedVFSEngine(file_path)
        node = handler.resolve_path(entity_path)

        if node is None:
            return f"Error: Entity not found: {entity_path}"

        # Format based on node type
        if hasattr(node, 'value'):
            return str(node.value)
        elif hasattr(node, 'children') and node.children:
            output = []
            children = list(node.children.items())[offset:offset + limit]
            for key, child in children:
                output.append(f"{key}: {child.brief or child.node_type.value}")
            return '\n'.join(output)

        return str(node)

    except Exception as e:
        return f"Error reading entity: {e}"


def pread_command(
    project_path: str,
    physical_file: str,
    args: list,
    current_cwd: str = ""
) -> str:
    """Read content from physical files and entities."""
    pontis_root = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        # Resolve physical file path
        resolved_physical = ctx.resolve_path(physical_file)
        physical_full_path = os.path.join(pontis_root, resolved_physical)

        if not os.path.exists(physical_full_path):
            return f"Error: Physical file not found: {physical_file}"

        # Parse arguments
        entity_name, limit, offset = parse_read_args(args)

        # Determine what to read
        if entity_name:
            # Reading an entity
            if entity_name.endswith('.table'):
                return read_table(pontis_root, resolved_physical, entity_name, limit, offset)
            elif entity_name.endswith('.col'):
                return read_column(pontis_root, resolved_physical, entity_name, limit, offset)
            elif '.json' in resolved_physical or '.yaml' in resolved_physical or '.yml' in resolved_physical:
                return read_serialized_entity(pontis_root, resolved_physical, entity_name, limit, offset)
            else:
                return f"Error: Unsupported entity type: {entity_name}"
        else:
            # Reading the physical file itself
            ext = os.path.splitext(resolved_physical)[1].lower()

            if ext == '.csv':
                return read_csv(physical_full_path, limit, offset)
            elif ext in ['.json', '.yaml', '.yml', '.py', '.js', '.ts', '.md', '.txt', '.sql']:
                return read_physical_file(physical_full_path, limit, offset)
            elif ext == '.db':
                return f"Error: Use 'pglob {physical_file} *.table' to list tables, then pread with entity name"
            else:
                # Try to read as text
                return read_physical_file(physical_full_path, limit, offset)

    except Exception as e:
        return f"Error reading: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    project_path = sys.argv[1]
    physical_file = sys.argv[2]
    args = sys.argv[3:]

    print(pread_command(project_path, physical_file, args))
