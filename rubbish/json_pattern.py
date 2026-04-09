"""
json_pattern.py - 从 JSON 文件中提取重复模式和语义实体

输出格式（每层一行，一层深度）：
  $path | DICT | each pair patterns "key_pat": value_type
  $path | DICT | each pair patterns {field: TYPE, ...}
  $path | ARRAY | each item patterns {field: TYPE, ...}
  $path | TYPE
"""
import json
import random
import re


# ============ 类型推导 ============

def get_base_type(value) -> str:
    if isinstance(value, bool): return "BOOL"
    if isinstance(value, int): return "INT"
    if isinstance(value, float): return "FLOAT"
    if isinstance(value, str): return "STR"
    if value is None: return "NULL"
    if isinstance(value, dict): return "DICT"
    if isinstance(value, list): return "ARRAY"
    return "UNKNOWN"


def type_label(schema) -> str:
    if isinstance(schema, str): return schema
    if isinstance(schema, dict): return "DICT"
    if isinstance(schema, list): return "ARRAY"
    return "UNKNOWN"


# ============ Schema 合并 ============

def merge_types(a: str, b: str) -> str:
    if a == b: return a
    return " | ".join(sorted(set(a.split(" | ")) | set(b.split(" | "))))


def make_nullable(schema) -> str:
    return merge_types(type_label(schema), "NULL")


def merge_schemas(s1, s2):
    if s1 == s2: return s1
    if isinstance(s1, dict) and isinstance(s2, dict):
        merged = {}
        for k in set(s1) | set(s2):
            if k in s1 and k in s2:
                merged[k] = merge_schemas(s1[k], s2[k])
            else:
                merged[k] = make_nullable(s1.get(k, s2.get(k)))
        return merged
    if isinstance(s1, list) and isinstance(s2, list):
        if not s1 or not s2: return s1 or s2
        return [merge_schemas(s1[0], s2[0])]
    if isinstance(s1, str) and isinstance(s2, str):
        return merge_types(s1, s2)
    return merge_types(type_label(s1), type_label(s2))


# ============ Schema 提取 ============

def extract_schema(data):
    if isinstance(data, dict):
        return {k: extract_schema(v) for k, v in data.items()}
    if isinstance(data, list):
        if not data: return ["EMPTY"]
        schema = extract_schema(data[0])
        for item in data[1:]:
            schema = merge_schemas(schema, extract_schema(item))
        return [schema]
    return get_base_type(data)


def extract_schema_sampled(data: list, max_items: int = 100) -> list:
    if len(data) <= max_items:
        return [extract_schema(data)]
    head, tail = data[:20], data[-20:]
    mid_n = max_items - 40
    mid_idx = random.sample(range(20, len(data) - 20), min(mid_n, len(data) - 40))
    sample = head + [data[i] for i in sorted(mid_idx)] + tail
    merged = extract_schema(sample[0])
    for item in sample[1:]:
        merged = merge_schemas(merged, extract_schema(item))
    return [merged]


def get_item_schema(data: list):
    if not data: return None
    schema = extract_schema_sampled(data)[0] if len(data) > 100 else extract_schema(data)
    if isinstance(schema, list) and schema: return schema[0]
    return schema


# ============ Key 模式检测（字符串匹配） ============

def _prefix_suffix_pattern(str_keys: list) -> str | None:
    """简单前缀+后缀匹配，中间用 ... 替代"""
    prefix = str_keys[0]
    for key in str_keys[1:]:
        while prefix and not key.startswith(prefix):
            prefix = prefix[:-1]

    suffix = str_keys[0]
    for key in str_keys[1:]:
        while suffix and not key.endswith(suffix):
            suffix = suffix[1:]

    # 没有任何固定部分则无意义
    if not prefix and not suffix:
        return None

    min_len = len(min(str_keys, key=len))
    if len(prefix) + len(suffix) >= min_len:
        if prefix:
            return f"{prefix}..."
        return None
    return f"{prefix}...{suffix}"


def detect_key_pattern(keys: list) -> str | None:
    """通过字符串匹配检测 key 的统一模式，变化部分用 ... 替代。

    按数字/非数字边界分割 token，逐 token 比较，
    相同的保留，不同的用 ... 替代。
    若 token 结构不一致或只有单个 token，回退到前缀+后缀匹配。
    """
    if len(keys) < 2:
        return None

    str_keys = [str(k) for k in keys]
    tokenized = [re.findall(r'\d+|\D+', k) for k in str_keys]
    token_counts = set(len(t) for t in tokenized)

    if len(token_counts) == 1 and len(tokenized[0]) > 1:
        n_tokens = len(tokenized[0])
        result_parts = []
        has_variation = False
        for i in range(n_tokens):
            values = set(t[i] for t in tokenized)
            if len(values) == 1:
                result_parts.append(values.pop())
            else:
                result_parts.append("...")
                has_variation = True

        if has_variation:
            # 合并连续的 ...
            merged = [result_parts[0]]
            for part in result_parts[1:]:
                if part == "..." and merged[-1] == "...":
                    continue
                merged.append(part)
            return "".join(merged)

    # 回退到前缀+后缀匹配
    return _prefix_suffix_pattern(str_keys)


# ============ 内联格式化（一层深度） ============

def format_schema_inline(schema) -> str:
    """将 schema 格式化为一层深度。ARRAY/DICT 不展开。"""
    if isinstance(schema, str):
        return schema
    if isinstance(schema, dict):
        if not schema:
            return "{}"
        pairs = []
        for k, v in schema.items():
            if isinstance(v, (dict, list)):
                pairs.append(f"{k}: {type_label(v)}")
            else:
                pairs.append(f"{k}: {format_schema_inline(v)}")
        return "{" + ", ".join(pairs) + "}"
    if isinstance(schema, list):
        return "ARRAY"
    return "UNKNOWN"


# ============ Map 检测 ============

def _jaccard_similarity(sets: list[set]) -> float:
    if len(sets) < 2: return 1.0
    total, count = 0.0, 0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = len(sets[i] | sets[j])
            if union == 0: continue
            total += len(sets[i] & sets[j]) / union
            count += 1
    return total / count if count else 0.0


def detect_map(data: dict) -> dict | None:
    """检测 dict 是否为 map-like（所有 value 同类型同结构）。"""
    if len(data) < 2:
        return None

    values = list(data.values())
    first_type = type(values[0])

    if first_type not in (dict, list):
        return None
    if not all(isinstance(v, first_type) for v in values):
        return None

    keys = list(data.keys())
    key_type = "INT" if all(isinstance(k, int) for k in keys) else "STR"
    key_pattern = detect_key_pattern(keys)

    if first_type == dict:
        key_sets = [set(v.keys()) for v in values]

        # 无 key 模式且 >2 个值时，做 Jaccard 相似度检查
        if key_pattern is None and len(values) > 2:
            if _jaccard_similarity(key_sets) < 0.3:
                return None

        # 所有 value 的 key 集合一致 → 内联展示 value schema
        # 否则只展示 "DICT"，由递归条目展示内部结构
        all_same_keys = len(set(frozenset(ks) for ks in key_sets)) == 1
        if all_same_keys:
            value_schema = get_item_schema(values)
        else:
            value_schema = "DICT"
    else:  # list
        merged = extract_schema(values[0])
        for v in values[1:]:
            merged = merge_schemas(merged, extract_schema(v))
        value_schema = merged

    return {
        "key_type": key_type,
        "key_pattern": key_pattern,
        "value_schema": value_schema,
    }


# ============ 条目收集 ============

def collect_entities(data, path: str, entities: list, max_depth: int = 20, _depth: int = 0):
    if _depth > max_depth:
        return

    if isinstance(data, dict):
        if len(data) < 2:
            # 不足 2 个 key，不构成重复模式，直接递归子项
            for k, v in data.items():
                if isinstance(v, (dict, list)):
                    collect_entities(v, f"{path}.{k}", entities, max_depth, _depth + 1)
            return

        map_info = detect_map(data)
        if map_info:
            entities.append({"path": path, "type": "map", **map_info})
            first_val = list(data.values())[0]
            collect_entities(first_val, f"{path}.[v]", entities, max_depth, _depth + 1)
        else:
            schema = extract_schema(data)
            entities.append({"path": path, "type": "dict", "schema": schema})
            for k, v in data.items():
                if isinstance(v, (dict, list)):
                    collect_entities(v, f"{path}.{k}", entities, max_depth, _depth + 1)

    elif isinstance(data, list):
        if len(data) < 2:
            # 不足 2 个元素，不构成重复模式
            if len(data) == 1:
                # 单元素：递归进入该元素，路径不变
                collect_entities(data[0], path, entities, max_depth, _depth + 1)
            # 空数组：无子项可递归
            return

        item_schema = get_item_schema(data)
        entities.append({
            "path": path, "type": "array",
            "item_schema": item_schema,
        })

        child_path = f"{path}.[n]"
        if isinstance(item_schema, dict):
            if data[0]:
                for k, v in data[0].items():
                    if isinstance(v, (dict, list)):
                        collect_entities(v, f"{child_path}.{k}", entities, max_depth, _depth + 1)
        elif isinstance(item_schema, list):
            collect_entities(data[0], child_path, entities, max_depth, _depth + 1)

    else:
        entities.append({"path": path, "type": get_base_type(data)})


# ============ 输出格式化 ============

def format_entity(entity: dict) -> str:
    path = entity["path"]
    etype = entity["type"]

    if etype == "array":
        schema_str = format_schema_inline(entity.get("item_schema"))
        return f"{path} | ARRAY | each item patterns {schema_str}"

    if etype == "array_empty":
        return f"{path} | ARRAY"

    if etype == "map":
        key_pattern = entity.get("key_pattern")
        key_type = entity.get("key_type", "STR")
        value_schema = entity.get("value_schema")
        key_desc = f'"{key_pattern}"' if key_pattern else key_type
        value_str = format_schema_inline(value_schema)
        return f'{path} | DICT | each pair patterns {key_desc}: {value_str}'

    if etype == "dict":
        schema = entity.get("schema", {})
        return f"{path} | DICT | each pair patterns {format_schema_inline(schema)}"

    return f"{path} | {etype}"


# ============ 主入口 ============

def analyze_json_file(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return f"Error reading {file_path}: {e}"

    entities = []
    collect_entities(data, "$", entities)
    return "\n".join(format_entity(e) for e in entities)


# ============ 测试 ============

if __name__ == "__main__":
    tests = [
        ("1_deep_nest", [
            {"user": "alice", "sessions": [
                {"id": 1, "events": [{"type": "click", "ts": 100}, {"type": "scroll"}]},
                {"id": 2, "events": [{"type": "click", "ts": 200}]}
            ]},
            {"user": "bob", "sessions": [
                {"id": 1, "events": [{"type": "click", "ts": 300}, {"type": "keydown", "ts": 301}]},
                {"id": 2, "events": []}
            ]}
        ]),
        ("2_arr_of_arr", [[1, 2, 3], [4, 5], [6]]),
        ("3_union_optional", [
            {"name": "a", "value": 10, "tag": "x"},
            {"name": "b", "value": "hello", "tag": "y"},
            {"name": "c"},
            {"name": "d", "value": 3.14}
        ]),
        ("4_map_list_val", {
            "row_01": [10, 20, 30], "row_02": [40, 50], "row_10": [60]
        }),
        ("5_record", {
            "title": "Report", "version": 2, "active": True,
            "metadata": {"author": "admin", "created": "2024-01-01"},
            "tags": ["finance", "q1"],
            "data": [[1, 2], [3, 4]]
        }),
        ("6_nested_map", {
            "dept_01": {"emp_01": {"name": "Alice", "role": "eng"}, "emp_02": {"name": "Bob", "role": "eng"}},
            "dept_02": {"emp_10": {"name": "Carol", "role": "mgr"}}
        }),
        ("7_non_monotonic", {"col_3": {"val": 1}, "col_1": {"val": 2}, "col_2": {"val": 3}}),
        ("8_single_key", {"only_one": {"a": 1, "b": 2}}),
        ("9_empty_arr", []),
        ("10_empty_dict", {}),
        ("11_primitive", "just a string"),
        ("12_large_array", [
            {"id": i, "status": "ok", "payload": {"size": i * 10}} for i in range(200)
        ] + [{"id": 200, "status": "error", "payload": {"size": 0, "error": "timeout"}}]),
        ("13_mixed_children", {
            "config": {"debug": True, "port": 8080},
            "users": {"user_01": {"role": "admin"}, "user_02": {"role": "viewer"}, "user_03": {"role": "editor"}},
            "metadata": {"count": 3}
        }),
        ("14_triple_digit", {"doc_001": {"title": "a"}, "doc_002": {"title": "b"}, "doc_100": {"title": "c"}}),
        ("15_inconsistent_val", {"x": {"name": "a"}, "y": [1, 2, 3], "z": {"name": "b"}}),
    ]
    for name, data in tests:
        tmp = f"/tmp/{name}.json"
        with open(tmp, "w") as f:
            json.dump(data, f)
        print(f"=== {name} ===")
        print(analyze_json_file(tmp))
        print()
