"""
Lookup tool - Value-based search for data entities.

Performs value alignment / predicate matching on:
- .table / .col: SQL predicate matching (e.g., "INT > 100", "STR = 'active'")
- .json: Search non-nested primitive values (String, Number, Bool, NULL) and keys

Parameters:
    file_pattern: Glob pattern for files (e.g., "**/*.db", "**/*.json")
    type: Data type to search ("INT", "STR", "BOOL", "NUMBER", "NULL")
    predicate: Filter expression (e.g., "INT > 100", "STR = 'active'")
    output_mode: "distinct_count" (default) or "file_count"

Output formats:
    distinct_count: path:count:value per line
    file_count: path:count per line
"""
import os
import re
import sys
import glob as pyglob
import sqlite3
from typing import Optional, List, Tuple

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_use.utils.path_parser import parse_path_pattern


def _parse_predicate(predicate: str) -> Tuple[str, str, object]:
    """
    Parse a predicate expression into (field_ref, operator, value).

    Examples:
        "INT > 100" -> ("", ">", 100)
        "STR = 'active'" -> ("", "=", "active")
        "id > 5" -> ("id", ">", 5)
    """
    # Match: optional_field OP value
    patterns = [
        r"(\w+)?\s*(>=|<=|!=|>|<|=)\s*(.+)",
    ]

    for pat in patterns:
        m = re.match(pat, predicate.strip())
        if m:
            field = m.group(1) or ""
            op = m.group(2)
            val_str = m.group(3).strip()

            # Parse value
            if val_str.startswith("'") and val_str.endswith("'"):
                val = val_str[1:-1]
            elif val_str.startswith('"') and val_str.endswith('"'):
                val = val_str[1:-1]
            elif val_str.lower() == 'null':
                val = None
            elif val_str.lower() == 'true':
                val = True
            elif val_str.lower() == 'false':
                val = False
            else:
                try:
                    val = float(val_str) if '.' in val_str else int(val_str)
                except ValueError:
                    val = val_str

            return field, op, val

    return "", "=", predicate


def _apply_predicate(value, op: str, target) -> bool:
    """Apply a comparison predicate."""
    try:
        if value is None or target is None:
            if op == '=':
                return value is None and target is None
            if op == '!=':
                return not (value is None and target is None)
            return False

        # Try numeric comparison
        if isinstance(target, (int, float)):
            num_val = float(value) if not isinstance(value, (int, float)) else value
            if op == '>': return num_val > target
            if op == '<': return num_val < target
            if op == '>=': return num_val >= target
            if op == '<=': return num_val <= target
            if op == '=': return num_val == target
            if op == '!=': return num_val != target

        # String comparison
        str_val = str(value)
        str_target = str(target)
        if op == '=': return str_val == str_target
        if op == '!=': return str_val != str_target
        if op == '>': return str_val > str_target
        if op == '<': return str_val < str_target
        if op == '>=': return str_val >= str_target
        if op == '<=': return str_val <= str_target
    except (TypeError, ValueError):
        pass
    return False


def _lookup_db_columns(
    project_path: str,
    file_pattern: str,
    data_type: str,
    predicate: str,
    output_mode: str,
    cwd: str = ""
) -> str:
    """
    Lookup values in database columns matching the predicate.

    Scans all .col entities under matching .db files.
    """
    results = []
    pontis_root = os.path.join(project_path, ".pontis")

    if not os.path.exists(pontis_root):
        return "No .pontis directory found"

    # Find matching DB files
    search_root = os.path.join(project_path, cwd) if cwd else project_path
    db_files = pyglob.glob(os.path.join(search_root, file_pattern), recursive=True)

    field, op, target_val = _parse_predicate(predicate)

    for db_file in db_files:
        db_rel = os.path.relpath(db_file, project_path)
        entity_root = os.path.join(pontis_root, db_rel, "_entity")

        if not os.path.exists(entity_root):
            continue

        # Walk to find .col entities
        for root, dirs, files in os.walk(entity_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for d in dirs:
                if not d.endswith('.col'):
                    continue

                # Check data type match
                col_meta_path = os.path.join(root, d, "_meta.yml")
                if os.path.exists(col_meta_path):
                    with open(col_meta_path, 'r') as f:
                        col_meta = yaml.safe_load(f) or {}

                    col_data_type = col_meta.get('data_type', '').upper()
                    type_match = (
                        data_type.upper() in col_data_type or
                        data_type.upper() == col_data_type or
                        data_type.upper() == 'NUMBER' and col_data_type in ('INT', 'INTEGER', 'REAL', 'FLOAT', 'DOUBLE')
                    )
                    if not type_match:
                        continue

                # Parse column entity name: table.col_name.data_type.col
                parts = d.replace('.col', '').split('.')
                if len(parts) < 2:
                    continue
                table_name = parts[0]
                col_name = parts[1]

                # Resolve actual DB path
                db_meta_path = os.path.join(pontis_root, db_rel, "_meta.yml")
                if not os.path.exists(db_meta_path):
                    continue

                with open(db_meta_path, 'r') as f:
                    db_meta = yaml.safe_load(f) or {}

                source_path = db_meta.get('path')
                if not source_path:
                    continue

                db_path = os.path.join(project_path, source_path)
                if not os.path.exists(db_path):
                    continue

                # Query column values
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()

                    cursor.execute(f'SELECT DISTINCT "{col_name}" FROM "{table_name}"')
                    distinct_values = cursor.fetchall()
                    conn.close()

                    matching = []
                    for (val,) in distinct_values:
                        if _apply_predicate(val, op, target_val):
                            matching.append(val)

                    if matching:
                        entity_rel = os.path.relpath(os.path.join(root, d), entity_root)
                        if output_mode == 'distinct_count':
                            for val in matching[:50]:
                                results.append(f"{db_rel}::{entity_rel}:{len(matching)}:{val}")
                        else:  # file_count
                            results.append(f"{db_rel}::{entity_rel}:{len(matching)}")

                except Exception:
                    continue

    if not results:
        return "No matching values found"

    return '\n'.join(results)


def _lookup_json_values(
    project_path: str,
    file_pattern: str,
    data_type: str,
    predicate: str,
    output_mode: str,
    cwd: str = ""
) -> str:
    """
    Lookup values in JSON files.

    Searches non-nested primitive types and keys.
    """
    import json

    results = []
    search_root = os.path.join(project_path, cwd) if cwd else project_path
    json_files = pyglob.glob(os.path.join(search_root, file_pattern), recursive=True)

    _, op, target_val = _parse_predicate(predicate)

    # Map type to Python types
    type_map = {
        'STR': (str,),
        'STRING': (str,),
        'INT': (int,),
        'NUMBER': (int, float),
        'FLOAT': (float,),
        'BOOL': (bool,),
        'NULL': (type(None),),
    }
    target_types = type_map.get(data_type.upper(), (str, int, float, bool, type(None)))

    for json_file in json_files:
        json_rel = os.path.relpath(json_file, project_path)
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        matching = []

        def _walk_json(obj, path=""):
            """Walk JSON structure collecting matching primitive values and keys."""
            if isinstance(obj, dict):
                for key, val in obj.items():
                    child_path = f"{path}.{key}" if path else key
                    # Check key (keys are always strings)
                    if data_type.upper() in ('STR', 'STRING') and isinstance(target_val, str):
                        if _apply_predicate(key, op, target_val):
                            matching.append((f"{child_path}(key)", key))
                    # Check value if it's a primitive
                    if isinstance(val, target_types) or (val is None and type(None) in target_types):
                        if _apply_predicate(val, op, target_val):
                            matching.append((child_path, val))
                    elif isinstance(val, (dict, list)):
                        _walk_json(val, child_path)

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    child_path = f"{path}[{i}]"
                    if isinstance(item, target_types) or (item is None and type(None) in target_types):
                        if _apply_predicate(item, op, target_val):
                            matching.append((child_path, item))
                    elif isinstance(item, (dict, list)):
                        _walk_json(item, child_path)

        _walk_json(data)

        if matching:
            if output_mode == 'distinct_count':
                for path_str, val in matching[:50]:
                    results.append(f"{json_rel}::{path_str}:{len(matching)}:{val}")
            else:  # file_count
                results.append(f"{json_rel}:{len(matching)}")

    if not results:
        return "No matching values found"

    return '\n'.join(results)


def lookup_command(
    project_path: str,
    file_pattern: str,
    type: str,
    predicate: str,
    output_mode: str = "distinct_count",
    current_cwd: str = ""
) -> str:
    """
    Value-based search for data entities.

    Args:
        project_path: Path to project root
        file_pattern: Glob pattern for files
        type: Data type ("INT", "STR", "BOOL", "NUMBER", "NULL")
        predicate: Filter expression
        output_mode: "distinct_count" or "file_count"
        current_cwd: Current working directory

    Returns:
        Formatted results
    """
    # Determine file type from pattern
    ext_pattern = os.path.splitext(file_pattern)[1].lower()

    if ext_pattern in ('.db', '.sqlite', '.sqlite3'):
        return _lookup_db_columns(
            project_path, file_pattern, type, predicate, output_mode, current_cwd
        )
    elif ext_pattern in ('.json', '.jsonl'):
        return _lookup_json_values(
            project_path, file_pattern, type, predicate, output_mode, current_cwd
        )
    else:
        # Try both
        db_result = _lookup_db_columns(
            project_path, file_pattern, type, predicate, output_mode, current_cwd
        )
        json_result = _lookup_json_values(
            project_path, file_pattern, type, predicate, output_mode, current_cwd
        )
        results = []
        if db_result != "No matching values found":
            results.append(db_result)
        if json_result != "No matching values found":
            results.append(json_result)
        return '\n'.join(results) if results else "No matching values found"


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python -m tool_use.lookup.tool <project_path> <file_pattern> <type> <predicate> [output_mode] [cwd]")
        sys.exit(1)

    _project = sys.argv[1]
    _pattern = sys.argv[2]
    _type = sys.argv[3]
    _predicate = sys.argv[4]
    _mode = sys.argv[5] if len(sys.argv) > 5 else "distinct_count"
    _cwd = sys.argv[6] if len(sys.argv) > 6 else ""
    print(lookup_command(_project, _pattern, _type, _predicate, _mode, _cwd))
