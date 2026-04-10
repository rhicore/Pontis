"""
Read tool - Read content from files and logical entities.

Supports path::entity syntax:
    read "data.db::users.table"           # Read a table entity
    read "data.db::users.id.INT.col"      # Read a column entity
    read "config.json"                     # Read a physical file

Parameters match the spec:
    file_path: path::entity string
    offset: line/row offset (1-indexed for text)
    limit: max lines/rows
    sample: random sample count (for DB entities)
    pages: PDF page range
"""
import os
import sys
import sqlite3
from typing import Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_use.utils.path_parser import parse_path_pattern

# File type constants
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
PDF_EXTENSION = '.pdf'
NOTEBOOK_EXTENSION = '.ipynb'

# Entity suffixes
ENTITY_SUFFIXES = {'.table', '.view', '.col', '.fk', '.rel', '.overlap', '.chunk', '.summary'}


def _read_text_file(file_path: str, offset: int = 1, limit: Optional[int] = None) -> str:
    """Read a text file with cat -n format."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        total = len(lines)
        start = max(0, offset - 1)

        if start >= total:
            return f"<system-reminder>Warning: offset ({offset}) is beyond file length ({total} lines).</system-reminder>"

        if limit is not None:
            end = min(start + limit, total)
        else:
            end = total
            if end - start > 2000:
                end = start + 2000

        output = []
        for i, line in enumerate(lines[start:end], start=start + 1):
            output.append(f"{i}\t{line.rstrip()}")

        if end < total:
            remaining = total - end
            output.append(f"<system-reminder>File has {remaining} more lines after line {end} (total {total}).</system-reminder>")

        return '\n'.join(output)
    except Exception as e:
        return f"Error reading file: {e}"


def _read_image(file_path: str) -> str:
    """Read an image file (return metadata)."""
    try:
        size = os.path.getsize(file_path)
        size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
        return f"[Image: {os.path.basename(file_path)} ({size_str})]"
    except Exception as e:
        return f"Error reading image: {e}"


def _read_pdf(file_path: str, pages: Optional[str] = None) -> str:
    """Read a PDF file."""
    try:
        size = os.path.getsize(file_path)
        size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
        if pages:
            return f"[PDF: {os.path.basename(file_path)} ({size_str}) - pages {pages}]"
        return f"[PDF: {os.path.basename(file_path)} ({size_str}) - use pages parameter to extract pages]"
    except Exception as e:
        return f"Error reading PDF: {e}"


def _read_notebook(file_path: str, offset: int = 1, limit: Optional[int] = None) -> str:
    """Read a Jupyter notebook file."""
    import json
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            notebook = json.load(f)

        cells = notebook.get('cells', [])
        total = len(cells)
        if total == 0:
            return "(empty notebook)"

        start = max(0, offset - 1)
        if start >= total:
            return f"<system-reminder>offset ({offset}) beyond notebook length ({total} cells).</system-reminder>"

        end = min(start + (limit or 20), total)

        output = []
        for i, cell in enumerate(cells[start:end], start=start):
            cell_type = cell.get('cell_type', 'unknown')
            source = ''.join(cell.get('source', []))
            output.append(f"{i}\t[{cell_type}]")
            for line in source.split('\n'):
                output.append(f"{i}\t{line}")
            output.append("")

        if end < total:
            output.append(f"... ({total - end} more cells)")
        return '\n'.join(output)
    except Exception as e:
        return f"Error reading notebook: {e}"


def _resolve_db_path(pontis_root: str, file_rel_path: str) -> Optional[str]:
    """Resolve the actual database file path from pontis metadata."""
    meta_path = os.path.join(pontis_root, file_rel_path, "_meta.yml")
    if not os.path.exists(meta_path):
        return None

    with open(meta_path, 'r') as f:
        meta = yaml.safe_load(f) or {}

    source_path = meta.get('path')
    if not source_path:
        return None

    # source_path is relative to project root
    # pontis_root is <project>/.pontis, so project root is parent
    db_path = os.path.join(os.path.dirname(pontis_root), source_path)
    return db_path if os.path.exists(db_path) else None


def _read_db_table(db_path: str, table_name: str,
                   offset: int = 0, limit: Optional[int] = None,
                   sample: Optional[int] = None) -> str:
    """Read a database table entity."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get column info
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()
        if not columns_info:
            conn.close()
            return f"Error: Table '{table_name}' not found"

        column_names = [col[1] for col in columns_info]
        pk_column = next((col[1] for col in columns_info if col[5] == 1), None)

        if limit is None:
            limit = 50

        if sample:
            # Random sampling
            cursor.execute(
                f'SELECT * FROM "{table_name}" ORDER BY RANDOM() LIMIT ?',
                (sample,)
            )
        elif pk_column:
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
        # Header
        header = "\t".join(column_names)
        output.append(header)
        # Data
        for row in rows:
            values = ["NULL" if v is None else str(v) for v in row]
            output.append("\t".join(values))

        if len(rows) >= limit and not sample:
            output.append(f"... (more rows)")

        return '\n'.join(output)
    except Exception as e:
        return f"Error reading table: {e}"


def _read_db_column(db_path: str, table_name: str, column_name: str,
                    offset: int = 0, limit: Optional[int] = None) -> str:
    """Read a database column entity."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()
        pk_column = next((col[1] for col in columns_info if col[5] == 1), None)

        if limit is None:
            limit = 100

        if pk_column:
            cursor.execute(
                f'SELECT "{pk_column}", "{column_name}" FROM "{table_name}" LIMIT ? OFFSET ?',
                (limit, offset)
            )
        else:
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
        output.append(f"{pk_column}\t{column_name}")
        for pk_val, col_val in rows:
            pk_str = "NULL" if pk_val is None else str(pk_val)
            val_str = "NULL" if col_val is None else str(col_val)
            output.append(f"{pk_str}\t{val_str}")

        if len(rows) >= limit:
            output.append("... (more values)")

        return '\n'.join(output)
    except Exception as e:
        return f"Error reading column: {e}"


def _read_chunk(pontis_root: str, file_rel_path: str, chunk_name: str) -> str:
    """Read a .chunk entity's raw content."""
    chunk_dir = os.path.join(pontis_root, file_rel_path, "_entity", chunk_name)
    raw_file = os.path.join(chunk_dir, "_raw")

    if not os.path.exists(raw_file):
        return f"Chunk content not found: {chunk_name}"

    try:
        with open(raw_file, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        return f"Error reading chunk: {e}"


def _read_json_entity(pontis_root: str, file_rel_path: str, entity_path: str) -> str:
    """Read a JSON/YAML pattern entity's metadata."""
    import yaml as _yaml

    entity_dir = os.path.join(pontis_root, file_rel_path, "_entity", entity_path)
    meta_path = os.path.join(entity_dir, "_meta.yml")

    if not os.path.exists(meta_path):
        return f"Entity not found: {entity_path}"

    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = _yaml.safe_load(f) or {}

    lines = []
    for key, value in sorted(meta.items()):
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def read_command(
    project_path: str,
    file_path: str,
    offset: int = 1,
    limit: Optional[int] = None,
    sample: Optional[int] = None,
    pages: Optional[str] = None,
    current_cwd: str = ""
) -> str:
    """
    Read content from files and entities.

    Args:
        project_path: Path to project root
        file_path: File path, optionally with ::entity suffix
        offset: Starting line/row (1-indexed for text)
        limit: Max lines/rows to read
        sample: Random sample count (for DB entities)
        pages: PDF page range
        current_cwd: Current working directory

    Returns:
        File/entity content as string
    """
    parsed = parse_path_pattern(file_path)
    pontis_root = os.path.join(project_path, ".pontis")

    # Resolve base file path
    if current_cwd and not os.path.isabs(parsed.file_pattern):
        resolved_file = os.path.join(current_cwd, parsed.file_pattern)
    else:
        resolved_file = parsed.file_pattern

    # If entity specified, read entity
    if parsed.has_entity:
        return _read_entity(pontis_root, project_path, resolved_file,
                            parsed.entity_pattern, offset, limit, sample)

    # Physical file read
    physical_path = os.path.join(project_path, resolved_file)
    if not os.path.exists(physical_path):
        return f"Error: File not found: {file_path}"

    ext = os.path.splitext(resolved_file)[1].lower()

    if ext in IMAGE_EXTENSIONS:
        return _read_image(physical_path)
    elif ext == PDF_EXTENSION:
        return _read_pdf(physical_path, pages)
    elif ext == NOTEBOOK_EXTENSION:
        return _read_notebook(physical_path, offset, limit)
    elif ext == '.csv':
        return _read_text_file(physical_path, offset, limit)
    else:
        return _read_text_file(physical_path, offset, limit)


def _read_entity(pontis_root: str, project_path: str, file_rel_path: str,
                 entity_path: str, offset: int, limit: Optional[int],
                 sample: Optional[int]) -> str:
    """Read a logical entity."""
    if entity_path.endswith('.chunk'):
        return _read_chunk(pontis_root, file_rel_path, entity_path)

    if entity_path.endswith('.table'):
        db_path = _resolve_db_path(pontis_root, file_rel_path)
        if not db_path:
            return f"Error: Database not found for {file_rel_path}"
        table_name = entity_path.replace('.table', '')
        return _read_db_table(db_path, table_name, offset, limit, sample)

    if entity_path.endswith('.col'):
        db_path = _resolve_db_path(pontis_root, file_rel_path)
        if not db_path:
            return f"Error: Database not found for {file_rel_path}"
        parts = entity_path.replace('.col', '').split('.')
        if len(parts) < 2:
            return f"Error: Invalid column entity: {entity_path}"
        table_name = parts[0]
        column_name = parts[1]
        return _read_db_column(db_path, table_name, column_name, offset, limit)

    # Try JSON entity
    ext = os.path.splitext(file_rel_path)[1].lower()
    if ext in ('.json', '.yaml', '.yml'):
        return _read_json_entity(pontis_root, file_rel_path, entity_path)

    return f"Error: Unsupported entity type: {entity_path}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.read.tool <project_path> <file_path> [options]")
        sys.exit(1)

    _project = sys.argv[1]
    _file = sys.argv[2]
    _offset = 1
    _limit = None
    _sample = None
    _pages = None

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] in ('-o', '--offset') and i + 1 < len(sys.argv):
            _offset = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] in ('-l', '--limit') and i + 1 < len(sys.argv):
            _limit = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] in ('-s', '--sample') and i + 1 < len(sys.argv):
            _sample = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] in ('-p', '--pages') and i + 1 < len(sys.argv):
            _pages = sys.argv[i + 1]; i += 2
        else:
            i += 1

    print(read_command(_project, _file, _offset, _limit, _sample, _pages))
