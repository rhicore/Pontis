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
import sqlite3
import glob as _glob
from typing import Optional, List, Tuple

from tool_use.utils.config import TOOL_PAGINATION


def _parse_predicate(predicate: str) -> Tuple[str, str, object]:
    """Parse a predicate expression into (field_ref, operator, value)."""
    patterns = [
        r"(\w+)?\s*(>=|<=|!=|>|<|=)\s*(.+)",
    ]

    for pat in patterns:
        m = re.match(pat, predicate.strip())
        if m:
            field = m.group(1) or ""
            op = m.group(2)
            val_str = m.group(3).strip()

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

        if isinstance(target, (int, float)):
            num_val = float(value) if not isinstance(value, (int, float)) else value
            if op == '>': return num_val > target
            if op == '<': return num_val < target
            if op == '>=': return num_val >= target
            if op == '<=': return num_val <= target
            if op == '=': return num_val == target
            if op == '!=': return num_val != target

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
    store,
    file_pattern: str,
    data_type: str,
    predicate: str,
    output_mode: str,
    cwd: str = ""
) -> str:
    """Lookup values in database columns matching the predicate."""
    results = []

    if not store.pontis_exists:
        return "No .pontis directory found"

    # Find matching DB files
    search_base = os.path.join(store.project_path, cwd) if cwd else store.project_path
    full_pattern = os.path.join(search_base, file_pattern)
    matched_paths = _glob.glob(full_pattern)
    db_files = [os.path.relpath(p, store.project_path) for p in matched_paths]

    field, op, target_val = _parse_predicate(predicate)

    for db_rel in db_files:
        # Find .col entities via store
        entity_refs = store.find_connected(db_rel, pattern="*.col")

        db_meta = store.get_meta(db_rel) or {}
        db_path = os.path.join(store.project_path, db_meta.get("path", ""))
        if not db_meta.get("path"):
            continue

        for entity_ref in entity_refs:
            entity_rel = entity_ref.split("::", 1)[-1] if "::" in entity_ref else entity_ref
            # Parse column entity name: table.col_name.data_type.col
            basename = os.path.basename(entity_rel)
            parts = basename.replace('.col', '').split('.')
            if len(parts) < 2:
                continue
            table_name = parts[0]
            col_name = parts[1]
            col_type = parts[2].upper() if len(parts) > 2 else ""

            # Check data type match from entity name
            type_match = (
                data_type.upper() in col_type or
                data_type.upper() == col_type or
                data_type.upper() == 'NUMBER' and col_type in ('INT', 'INTEGER', 'REAL', 'FLOAT', 'DOUBLE')
            )
            if not type_match:
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
                    if output_mode == 'distinct_count':
                        for val in matching:
                            results.append(f"{db_rel}::{entity_rel}:{len(matching)}:{val}")
                    else:
                        results.append(f"{db_rel}::{entity_rel}:{len(matching)}")

            except Exception:
                continue

    if not results:
        return "No matching values found"

    return '\n'.join(results)


def _lookup_json_values(
    store,
    file_pattern: str,
    data_type: str,
    predicate: str,
    output_mode: str,
    cwd: str = ""
) -> str:
    """Lookup values in JSON files."""
    import json

    results = []
    search_base = os.path.join(store.project_path, cwd) if cwd else store.project_path
    full_pattern = os.path.join(search_base, file_pattern)
    matched_paths = _glob.glob(full_pattern)
    json_files = [os.path.relpath(p, store.project_path) for p in matched_paths]

    _, op, target_val = _parse_predicate(predicate)

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
        full_path = os.path.join(store.project_path, json_file)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        matching = []

        def _walk_json(obj, path=""):
            if isinstance(obj, dict):
                for key, val in obj.items():
                    child_path = f"{path}.{key}" if path else key
                    if data_type.upper() in ('STR', 'STRING') and isinstance(target_val, str):
                        if _apply_predicate(key, op, target_val):
                            matching.append((f"{child_path}(key)", key))
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
                for path_str, val in matching:
                    results.append(f"{json_file}::{path_str}:{len(matching)}:{val}")
            else:
                results.append(f"{json_file}:{len(matching)}")

    if not results:
        return "No matching values found"

    return '\n'.join(results)


def lookup_command(
    store,
    file_pattern: str,
    type: str,
    predicate: str,
    output_mode: str = "distinct_count",
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = ""
) -> str:
    """
    Value-based search for data entities.

    Args:
        store: Store instance
        file_pattern: Glob pattern for files
        type: Data type ("INT", "STR", "BOOL", "NUMBER", "NULL")
        predicate: Filter expression
        output_mode: "distinct_count" or "file_count"
        offset: Starting index (0-based)
        limit: Max results per page
        current_cwd: Current working directory

    Returns:
        Formatted results
    """
    page_conf = TOOL_PAGINATION["lookup"]
    if limit is None:
        limit = page_conf.default_limit
    limit = min(limit, page_conf.max_limit)

    ext_pattern = os.path.splitext(file_pattern)[1].lower()

    all_results = []

    def _collect(result_str: str):
        if result_str != "No matching values found":
            for line in result_str.split('\n'):
                all_results.append(line)

    if ext_pattern in ('.db', '.sqlite', '.sqlite3'):
        _collect(_lookup_db_columns(
            store, file_pattern, type, predicate, output_mode, current_cwd
        ))
    elif ext_pattern in ('.json', '.jsonl'):
        _collect(_lookup_json_values(
            store, file_pattern, type, predicate, output_mode, current_cwd
        ))
    else:
        _collect(_lookup_db_columns(
            store, file_pattern, type, predicate, output_mode, current_cwd
        ))
        _collect(_lookup_json_values(
            store, file_pattern, type, predicate, output_mode, current_cwd
        ))

    if not all_results:
        return "No matching values found"

    total = len(all_results)
    page = all_results[offset:offset + limit]

    if not page:
        return f"No results at offset {offset}. Total results: {total}"

    output = '\n'.join(page)

    end = offset + len(page)
    if end < total:
        output += f"\n(共 {total} 条结果，当前显示第 {offset + 1}-{end} 条。使用 offset={end} 查看后续结果)"

    return output


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("Usage: python -m tool_use.lookup.tool <project_path> <file_pattern> <type> <predicate> [output_mode] [cwd]")
        sys.exit(1)

    from storage import Store
    _store = Store(sys.argv[1])
    _pattern = sys.argv[2]
    _type = sys.argv[3]
    _predicate = sys.argv[4]
    _mode = sys.argv[5] if len(sys.argv) > 5 else "distinct_count"
    _cwd = sys.argv[6] if len(sys.argv) > 6 else ""
    print(lookup_command(_store, _pattern, _type, _predicate, _mode, _cwd))
