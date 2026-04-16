"""
Lookup tool - Value-based search for data entities.

三层查询策略：
  Layer 1: 元数据预过滤（min/max/cardinality，不碰索引和SQL）
  Layer 2: LSH 索引查询（O(1) 等值，O(1) 范围预判）
  Layer 3: SQL 兜底（仅索引无法确定时，带谓词下推）
"""
import os
import re
import sqlite3
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


# ── Index helpers ──────────────────────────────────────────────

def _load_index(store, ref: str):
    """通过 KG 查找索引节点，加载 LSH 索引。不存在返回 None。"""
    idx_refs = store.find_connected(ref, edge_type="contains", pattern="*.idx")
    if not idx_refs:
        return None
    try:
        from extractor.modules._lsh_index import LSHIndexReader
        idx_meta = store._get_stored_meta(idx_refs[0]) or {}
        idx_rel = idx_meta.get("path")
        if not idx_rel:
            return None
        idx_path = os.path.join(store.project_path, idx_rel)
        return LSHIndexReader.load(idx_path)
    except (ImportError, Exception):
        return None


def _index_lookup(reader, op: str, target_val, col_meta: dict) -> Tuple[bool, bool]:
    """索引查询。

    Returns:
        (has_match, need_sql): has_match=False 表示确定无匹配，
                               need_sql=False 表示索引已直接回答。
    """
    if reader is None:
        return (True, True)

    # Layer 1: 元数据预过滤
    if op == '=' and target_val is not None:
        min_v = col_meta.get('min_value')
        max_v = col_meta.get('max_value')
        if min_v is not None and isinstance(target_val, (int, float)):
            try:
                if float(target_val) < float(min_v):
                    return (False, False)
            except (TypeError, ValueError):
                pass
        if max_v is not None and isinstance(target_val, (int, float)):
            try:
                if float(target_val) > float(max_v):
                    return (False, False)
            except (TypeError, ValueError):
                pass

    # Layer 2: 索引查询
    if op == '=':
        return (reader.query_eq(target_val), False)

    if op == '!=':
        exists = reader.query_eq(target_val)
        cardinality = col_meta.get('cardinality', 2)
        if not exists:
            return (True, False)  # 值不存在，所有行都 !=
        if cardinality <= 1:
            return (False, False)  # 只有一个值且就是它，无匹配
        return (True, False)

    # 范围查询（数值）
    if op in ('>', '<', '>=', '<=') and reader.has_kll:
        try:
            threshold = float(target_val)
            if op == '>':
                est = reader.query_range_gt(threshold)
            elif op == '<':
                est = reader.query_range_lt(threshold)
            elif op == '>=':
                est = reader.query_range_gte(threshold)
            else:  # <=
                est = reader.query_range_lte(threshold)
            if est == 0:
                return (False, False)
        except (TypeError, ValueError):
            pass

    return (True, True)  # 需 SQL 兜底


# ── DB lookup ──────────────────────────────────────────────────

def _lookup_db_columns(
    store,
    file_pattern: str,
    data_type: str,
    predicate: str,
    output_mode: str,
) -> str:
    """Lookup values in database columns matching the predicate."""
    results = []

    if not store.pontis_exists:
        return "No .pontis directory found"

    db_refs = store.find_nodes(file_pattern)
    db_refs = [r for r in db_refs if "::" not in r]

    field, op, target_val = _parse_predicate(predicate)

    for db_ref in db_refs:
        db_meta = store.get_meta(db_ref) or {}
        db_path = os.path.join(store.project_path, db_meta.get("path", ""))
        if not db_meta.get("path"):
            continue

        entity_refs = store.find_connected(db_ref, edge_type="contains", pattern="*.col")

        for entity_ref in entity_refs:
            entity_rel = entity_ref.split("::", 1)[-1] if "::" in entity_ref else entity_ref
            basename = os.path.basename(entity_rel)
            parts = basename.replace('.col', '').split('.')
            if len(parts) < 2:
                continue
            table_name = parts[0]
            col_name = parts[1]
            col_type = parts[2].upper() if len(parts) > 2 else ""

            # 类型过滤
            _type = data_type.upper()
            _STR_TYPES = ('STR', 'STRING', 'TEXT', 'VARCHAR', 'CHAR')
            _NUM_TYPES = ('INT', 'INTEGER', 'REAL', 'FLOAT', 'DOUBLE', 'NUMERIC')
            type_match = (
                _type in col_type or
                _type == col_type or
                _type == 'NUMBER' and col_type in _NUM_TYPES or
                _type == 'STR' and col_type in _STR_TYPES or
                _type in _STR_TYPES and col_type in _STR_TYPES
            )
            if not type_match:
                continue

            # 获取列元数据
            col_meta = store._get_stored_meta(entity_ref) or {}

            # 尝试索引查询
            reader = _load_index(store, entity_ref)
            has_match, need_sql = _index_lookup(reader, op, target_val, col_meta)

            if not has_match:
                continue

            if not need_sql:
                # 索引直接回答，无需 SQL
                if output_mode == 'distinct_count' and op == '=':
                    results.append(f"{db_ref}::{entity_rel}:1:{target_val}")
                else:
                    results.append(f"{db_ref}::{entity_rel}:1")
                continue

            # Layer 3: SQL 兜底（带谓词下推）
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()

                if op == '=' and target_val is not None:
                    cursor.execute(
                        f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" = ?',
                        (target_val,))
                elif op == '!=' and target_val is not None:
                    cursor.execute(
                        f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" != ?',
                        (target_val,))
                elif op in ('>', '<', '>=', '<=') and isinstance(target_val, (int, float)):
                    cursor.execute(
                        f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" {op} ?',
                        (target_val,))
                else:
                    cursor.execute(f'SELECT DISTINCT "{col_name}" FROM "{table_name}"')

                distinct_values = cursor.fetchall()
                conn.close()

                matching = [v for (v,) in distinct_values
                            if _apply_predicate(v, op, target_val)]

                if matching:
                    if output_mode == 'distinct_count':
                        for val in matching:
                            results.append(f"{db_ref}::{entity_rel}:{len(matching)}:{val}")
                    else:
                        results.append(f"{db_ref}::{entity_rel}:{len(matching)}")

            except Exception:
                continue

    if not results:
        return "No matching values found"

    return '\n'.join(results)


# ── JSON lookup ────────────────────────────────────────────────

def _lookup_json_values(
    store,
    file_pattern: str,
    data_type: str,
    predicate: str,
    output_mode: str,
) -> str:
    """Lookup values in JSON files."""
    import json

    results = []

    json_refs = store.find_nodes(file_pattern)
    json_refs = [r for r in json_refs if "::" not in r]

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

    for json_ref in json_refs:
        json_meta = store.get_meta(json_ref) or {}
        json_file = json_meta.get("path", json_ref)
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
                    results.append(f"{json_ref}::{path_str}:{len(matching)}:{val}")
            else:
                results.append(f"{json_ref}:{len(matching)}")

    if not results:
        return "No matching values found"

    return '\n'.join(results)


# ── Entry point ────────────────────────────────────────────────

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
        file_pattern: Glob pattern for files (matched via store.find_nodes)
        type: Data type ("INT", "STR", "BOOL", "NUMBER", "NULL")
        predicate: Filter expression
        output_mode: "distinct_count" or "file_count"
        offset: Starting index (0-based)
        limit: Max results per page
        current_cwd: Current working directory (unused)

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
        _collect(_lookup_db_columns(store, file_pattern, type, predicate, output_mode))
    elif ext_pattern in ('.json', '.jsonl'):
        _collect(_lookup_json_values(store, file_pattern, type, predicate, output_mode))
    else:
        _collect(_lookup_db_columns(store, file_pattern, type, predicate, output_mode))
        _collect(_lookup_json_values(store, file_pattern, type, predicate, output_mode))

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
        print("Usage: python -m tool_use.lookup.tool <project_path> <file_pattern> <type> <predicate> [output_mode]")
        sys.exit(1)

    from storage import Store
    _store = Store(sys.argv[1])
    _pattern = sys.argv[2]
    _type = sys.argv[3]
    _predicate = sys.argv[4]
    _mode = sys.argv[5] if len(sys.argv) > 5 else "distinct_count"
    print(lookup_command(_store, _pattern, _type, _predicate, _mode))
