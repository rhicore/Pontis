"""read command - Read content from files and knowledge graph entities

Usage:
    # Read physical file
    python -m tool_use.read <project_path> <file_path> [options]

    # Read entity under physical file
    python -m tool_use.read <project_path> <physical_file> <entity_name> [options]

Options:
    -o, --offset <n>     Start from line n (1-indexed for text, 0-indexed for entities)
    -l, --limit <n>      Read at most n lines/rows
    -p, --pages <range>  PDF page range (e.g., "1-5", "3", "10-20")

Examples:
    # Text files
    python -m tool_use.read ./my_project script.py
    python -m tool_use.read ./my_project script.py -o 10 -l 50

    # Images
    python -m tool_use.read ./my_project screenshot.png

    # PDF
    python -m tool_use.read ./my_project document.pdf -p 1-5

    # Database entities
    python -m tool_use.read ./my_project mydb.db users.table
    python -m tool_use.read ./my_project mydb.db users.id.INT.col -l 100

    # CSV
    python -m tool_use.read ./my_project data.csv -l 20

    # Notebook
    python -m tool_use.read ./my_project analysis.ipynb
"""
import sys
import os
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext

# Image extensions
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

# PDF extension
PDF_EXTENSION = '.pdf'

# Notebook extension
NOTEBOOK_EXTENSION = '.ipynb'

# Entity suffixes (knowledge graph entities)
ENTITY_SUFFIXES = {'.table', '.view', '.col', '.fk', '.rel', '.overlap', '.chunk', '.summary'}


def parse_read_args(args: list) -> Tuple[Optional[str], int, Optional[int], Optional[str]]:
    """Parse read command arguments.

    Returns: (entity_name, offset, limit, pages)
    """
    entity_name = None
    offset = 1  # Default 1-indexed for text files
    limit = None
    pages = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ['-o', '--offset'] and i + 1 < len(args):
            try:
                offset = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ['-l', '--limit'] and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg in ['-p', '--pages'] and i + 1 < len(args):
            pages = args[i + 1]
            i += 2
        elif not arg.startswith('-'):
            # This is the entity name (first non-flag arg after physical file)
            if entity_name is None:
                # Check if it looks like an entity
                if any(arg.endswith(suffix) for suffix in ENTITY_SUFFIXES):
                    entity_name = arg
                else:
                    # Might be a file path continuation - skip
                    pass
            i += 1
        else:
            i += 1

    return entity_name, offset, limit, pages


def read_text_file(file_path: str, offset: int = 1, limit: Optional[int] = None, name: str = "") -> str:
    """Read a text file with format: [Index] | [Content] (Index is line number)."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        total_lines = len(lines)
        start = max(0, offset - 1)

        if start >= total_lines:
            return f"<system-reminder>Warning: offset ({offset}) is beyond file length ({total_lines} lines).</system-reminder>"

        if limit is not None:
            end = min(start + limit, total_lines)
        else:
            end = total_lines
            if end - start > 200:
                end = start + 200

        selected_lines = lines[start:end]

        output = []
        for i, line in enumerate(selected_lines, start=start + 1):
            # Format: [Index] | [Content] (Index = line number)
            output.append(f"{i} | {line.rstrip()}")

        if end < total_lines:
            remaining = total_lines - end
            output.append(f"... ({remaining} more lines, total {total_lines})")

        return '\n'.join(output)

    except Exception as e:
        return f"Error reading file: {e}"


def read_image(file_path: str) -> str:
    """Read an image file."""
    try:
        size = os.path.getsize(file_path)
        size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
        return f"[Image: {os.path.basename(file_path)} ({size_str})]"
    except Exception as e:
        return f"Error reading image: {e}"


def read_pdf(file_path: str, pages: Optional[str] = None) -> str:
    """Read a PDF file."""
    try:
        size = os.path.getsize(file_path)
        size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"

        if pages:
            return f"[PDF: {os.path.basename(file_path)} ({size_str}) - pages {pages}]"
        else:
            return f"[PDF: {os.path.basename(file_path)} ({size_str}) - use -p option to extract pages]"
    except Exception as e:
        return f"Error reading PDF: {e}"


def read_notebook(file_path: str, offset: int = 1, limit: Optional[int] = None, name: str = "") -> str:
    """Read a Jupyter notebook file with format: [Index] | [Content]."""
    try:
        import json

        with open(file_path, 'r', encoding='utf-8') as f:
            notebook = json.load(f)

        cells = notebook.get('cells', [])
        total_cells = len(cells)

        if total_cells == 0:
            return "(empty notebook)"

        start = max(0, offset - 1)

        if start >= total_cells:
            return f"<system-reminder>Warning: offset ({offset}) is beyond notebook length ({total_cells} cells).</system-reminder>"

        if limit is not None:
            end = min(start + limit, total_cells)
        else:
            end = total_cells
            if end - start > 20:
                end = start + 20

        output = []
        for i, cell in enumerate(cells[start:end], start=start):
            cell_type = cell.get('cell_type', 'unknown')
            source = ''.join(cell.get('source', []))

            # First line: cell index with type info
            # Content: cell source
            output.append(f"{i} | [{cell_type}]")
            # Add cell content lines
            for line in source.split('\n'):
                output.append(f"{i} | {line}")
            output.append("")  # Empty line between cells

        if end < total_cells:
            remaining = total_cells - end
            output.append(f"... ({remaining} more cells)")

        return '\n'.join(output)

    except Exception as e:
        return f"Error reading notebook: {e}"


def read_csv_file(file_path: str, offset: int = 0, limit: Optional[int] = None, name: str = "") -> str:
    """Read a CSV file as text with format: [Index] | [Content] (Index is line number)."""
    # CSV is treated as text file - use line number as index
    return read_text_file(file_path, offset + 1 if offset is not None else 1, limit, name)


def read_db_table(pontis_root: str, db_file: str, table_entity: str,
                  offset: int = 0, limit: Optional[int] = None) -> str:
    """Read a database table entity with format:
    - First line: [pk_name] | [column_names...]
    - Data lines: [pk_value] | [values...]
    """
    try:
        import sqlite3
        import yaml

        meta_path = os.path.join(pontis_root, db_file, '_meta.yml')
        if not os.path.exists(meta_path):
            return f"Error: Database metadata not found for {db_file}"

        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f) or {}

        source_path = meta.get('path')
        if not source_path:
            return f"Error: Database source path not found"

        db_path = os.path.join(os.path.dirname(pontis_root), source_path)
        if not os.path.exists(db_path):
            return f"Error: Database file not found: {db_path}"

        table_name = table_entity.replace('.table', '')

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()

        if not columns_info:
            conn.close()
            return f"Error: Table '{table_name}' not found"

        column_names = [col[1] for col in columns_info]
        pk_column = next((col[1] for col in columns_info if col[5] == 1), None)

        if limit is None:
            limit = 50

        # Query data
        if pk_column:
            cursor.execute(
                f'SELECT * FROM "{table_name}" ORDER BY "{pk_column}" LIMIT ? OFFSET ?',
                (limit, offset)
            )
        else:
            cursor.execute(
                f'SELECT * FROM "{table_name}" LIMIT ? OFFSET ?',
                (limit, offset)
            )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "(no data or offset beyond table length)"

        output = []

        # Header line: [pk_name] | [column_names...]
        if pk_column:
            other_cols = [c for c in column_names if c != pk_column]
            header = f"{pk_column} | {other_cols}"
        else:
            header = f"row_num | {column_names}"
        output.append(header)

        # Data lines: [pk_value] | [values...]
        for row_idx, row in enumerate(rows):
            values = []
            for val in row:
                if val is None:
                    values.append("NULL")
                else:
                    values.append(str(val))

            if pk_column:
                # Find pk value index
                pk_idx = column_names.index(pk_column)
                pk_value = values[pk_idx]
                other_values = [v for i, v in enumerate(values) if i != pk_idx]
                line = f"{pk_value} | {other_values}"
            else:
                # Use row number as index
                line = f"{offset + row_idx} | {values}"
            output.append(line)

        if len(rows) >= limit:
            output.append(f"... (more rows)")

        return '\n'.join(output)

    except Exception as e:
        return f"Error reading table: {e}"


def read_db_column(pontis_root: str, db_file: str, col_entity: str,
                   offset: int = 0, limit: Optional[int] = None) -> str:
    """Read a database column entity with format:
    - First line: [pk_name] | [column_name]
    - Data lines: [pk_value] | [column_value]
    """
    try:
        import sqlite3
        import yaml

        parts = col_entity.replace('.col', '').split('.')
        if len(parts) < 3:
            return f"Error: Invalid column entity name: {col_entity}"

        table_name = parts[0]
        column_name = parts[1]

        meta_path = os.path.join(pontis_root, db_file, '_meta.yml')
        if not os.path.exists(meta_path):
            return f"Error: Database metadata not found"

        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f) or {}

        source_path = meta.get('path')
        if not source_path:
            return f"Error: Database source path not found"

        db_path = os.path.join(os.path.dirname(pontis_root), source_path)
        if not os.path.exists(db_path):
            return f"Error: Database file not found"

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get PK column
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()
        pk_column = next((col[1] for col in columns_info if col[5] == 1), None)

        if limit is None:
            limit = 100

        # Query both PK and target column
        if pk_column:
            cursor.execute(
                f'SELECT "{pk_column}", "{column_name}" FROM "{table_name}" LIMIT ? OFFSET ?',
                (limit, offset)
            )
        else:
            # No PK, use rowid
            cursor.execute(
                f'SELECT rowid, "{column_name}" FROM "{table_name}" LIMIT ? OFFSET ?',
                (limit, offset)
            )
            pk_column = "rowid"

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "(no data)"

        output = []

        # Header line: [pk_name] | [column_name]
        output.append(f"{pk_column} | {column_name}")

        # Data lines: [pk_value] | [column_value]
        for pk_val, col_val in rows:
            pk_str = "NULL" if pk_val is None else str(pk_val)
            val_str = "NULL" if col_val is None else str(col_val)
            output.append(f"{pk_str} | {val_str}")

        if len(rows) >= limit:
            output.append(f"... (more values)")

        return '\n'.join(output)

    except Exception as e:
        return f"Error reading column: {e}"


def read_command(
    project_path: str,
    file_path: str,
    args: list = None,
    current_cwd: str = ""
) -> str:
    """Read content from files and entities.

    Usage:
        read <project> <file> [options]              # Read physical file
        read <project> <file> <entity> [options]     # Read entity
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

        # Parse arguments to check for entity
        entity_name, offset, limit, pages = parse_read_args(args)

        # Check if it's a knowledge graph entity read
        if entity_name:
            if not os.path.exists(pontis_full_path):
                return f"Error: File not found: {file_path}"

            if entity_name.endswith('.table'):
                return read_db_table(pontis_root, resolved_path, entity_name, offset, limit)
            elif entity_name.endswith('.col'):
                return read_db_column(pontis_root, resolved_path, entity_name, offset, limit)
            else:
                return f"Error: Unsupported entity type: {entity_name}"

        # Reading the physical file itself - read from project_path, not pontis_root
        # The .pontis directory contains metadata folders for each file
        physical_full_path = os.path.join(project_path, resolved_path)
        if not os.path.exists(physical_full_path):
            return f"Error: File not found: {file_path}"

        ext = os.path.splitext(resolved_path)[1].lower()
        display_name = os.path.basename(resolved_path)

        if ext in IMAGE_EXTENSIONS:
            return read_image(physical_full_path)
        elif ext == PDF_EXTENSION:
            return read_pdf(physical_full_path, pages)
        elif ext == NOTEBOOK_EXTENSION:
            return read_notebook(physical_full_path, offset, limit, display_name)
        elif ext == '.csv':
            return read_csv_file(physical_full_path, offset - 1 if offset else 0, limit, display_name)
        else:
            return read_text_file(physical_full_path, offset, limit, display_name)

    except Exception as e:
        return f"Error reading: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    project_path = sys.argv[1]
    file_path = sys.argv[2]
    args = sys.argv[3:]

    print(read_command(project_path, file_path, args))
