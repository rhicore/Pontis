"""JSON Pattern Generator - JSON 模式提取器

职责：
- 匹配所有 *.json VFS 节点
- 读取 JSON 数据
- 提取重复模式（ARRAY 2+ 元素，DICT 2+ key）
- 创建 pattern 子节点

pattern 节点通过边连接到源 JSON 文件，节点自身只保存 JSON 片段结构：
  json_path:   $.users.[v]
  type:        DICT                                                  # DICT 或 ARRAY
  pattern:     each pair patterns "user_...": {role: STR}           # 模式描述
"""
import json
import random
import re
import logging
from storage.workspace import Workspace
from tool.utils.workspace_access import OpenFileSource, resolve_file_sources

logger = logging.getLogger(__name__)


def pattern_entity_name(json_path: str) -> str:
    safe_path = re.sub(r"[^A-Za-z0-9._$\[\]-]+", "_", json_path).strip("._") or "$"
    return safe_path


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


# ============ Key 模式检测 ============

def _prefix_suffix_pattern(str_keys: list) -> str | None:
    prefix = str_keys[0]
    for key in str_keys[1:]:
        while prefix and not key.startswith(prefix):
            prefix = prefix[:-1]
    suffix = str_keys[0]
    for key in str_keys[1:]:
        while suffix and not key.endswith(suffix):
            suffix = suffix[1:]
    if not prefix and not suffix:
        return None
    min_len = len(min(str_keys, key=len))
    if len(prefix) + len(suffix) >= min_len:
        if prefix:
            return f"{prefix}..."
        return None
    return f"{prefix}...{suffix}"


def detect_key_pattern(keys: list) -> str | None:
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
            merged = [result_parts[0]]
            for part in result_parts[1:]:
                if part == "..." and merged[-1] == "...":
                    continue
                merged.append(part)
            return "".join(merged)
    return _prefix_suffix_pattern(str_keys)


# ============ 内联格式化（一层深度） ============

def format_schema_inline(schema) -> str:
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
        if key_pattern is None and len(values) > 2:
            if _jaccard_similarity(key_sets) < 0.3:
                return None
        all_same_keys = len(set(frozenset(ks) for ks in key_sets)) == 1
        if all_same_keys:
            value_schema = get_item_schema(values)
        else:
            value_schema = "DICT"
    else:
        merged = extract_schema(values[0])
        for v in values[1:]:
            merged = merge_schemas(merged, extract_schema(v))
        value_schema = merged
    return {
        "key_type": key_type,
        "key_pattern": key_pattern,
        "value_schema": value_schema,
    }


# ============ 模式条目收集 ============

def collect_patterns(data, path: str, patterns: list, max_depth: int = 20, _depth: int = 0):
    """收集所有重复模式条目。ARRAY 2+ 元素、DICT 2+ key 才生成条目。"""
    if _depth > max_depth:
        return

    if isinstance(data, dict):
        if len(data) < 2:
            for k, v in data.items():
                if isinstance(v, (dict, list)):
                    collect_patterns(v, f"{path}.{k}", patterns, max_depth, _depth + 1)
            return

        map_info = detect_map(data)
        if map_info:
            key_pattern = map_info.get("key_pattern")
            key_type = map_info.get("key_type", "STR")
            value_schema = map_info.get("value_schema")
            key_desc = f'"{key_pattern}"' if key_pattern else key_type
            value_str = format_schema_inline(value_schema)
            patterns.append({
                "name": path,
                "type": "DICT",
                "pattern": f"each pair patterns {key_desc}: {value_str}",
            })
            first_val = list(data.values())[0]
            collect_patterns(first_val, f"{path}.[v]", patterns, max_depth, _depth + 1)
        else:
            schema = extract_schema(data)
            patterns.append({
                "name": path,
                "type": "DICT",
                "pattern": f"each pair patterns {format_schema_inline(schema)}",
            })
            for k, v in data.items():
                if isinstance(v, (dict, list)):
                    collect_patterns(v, f"{path}.{k}", patterns, max_depth, _depth + 1)

    elif isinstance(data, list):
        if len(data) < 2:
            if len(data) == 1:
                collect_patterns(data[0], path, patterns, max_depth, _depth + 1)
            return

        item_schema = get_item_schema(data)
        patterns.append({
            "name": path,
            "type": "ARRAY",
            "pattern": f"each item patterns {format_schema_inline(item_schema)}",
        })

        child_path = f"{path}.[n]"
        if isinstance(item_schema, dict):
            if data[0]:
                for k, v in data[0].items():
                    if isinstance(v, (dict, list)):
                        collect_patterns(v, f"{child_path}.{k}", patterns, max_depth, _depth + 1)
        elif isinstance(item_schema, list):
            collect_patterns(data[0], child_path, patterns, max_depth, _depth + 1)


# ============ 主生成器 ============

def generate(workspace: Workspace, max_file_size: int | None = None) -> None:
    """为所有 JSON 文件生成 pattern 子实体"""
    logger.info("=== Generating JSON patterns ===")

    seen = set()
    for source in resolve_file_sources(workspace, ".", labels=("json",), allow_directory=True):
        if source.path in seen:
            continue
        seen.add(source.path)
        if max_file_size and source.file_size and int(source.file_size) > max_file_size:
            logger.info(
                "  JSON pattern skipped: %s (%.1f MB > %.1f MB limit)",
                source.path,
                int(source.file_size) / 1024 / 1024,
                max_file_size / 1024 / 1024,
            )
            continue
        try:
            _generate_for_json(source, workspace)
        except Exception as e:
            logger.warning(f"Failed to generate patterns for {source.path}: {e}")


def _generate_for_json(source: OpenFileSource, workspace: Workspace) -> bool:
    """为单个 JSON 文件生成 pattern 子实体"""
    # 读取 JSON 数据
    data = _load_json(source)
    if data is None:
        return False

    # 提取模式，根路径用 $
    patterns = []
    collect_patterns(data, "$", patterns)

    if not patterns:
        return False

    # 为每个模式创建 pattern 子实体
    for pat in patterns:
        _write_pattern(source.path, pat, workspace)

    logger.info(f"  Patterns: {source.path} ({len(patterns)} entities)")
    return True


def _load_json(source: OpenFileSource):
    """从源文件加载 JSON 数据"""
    try:
        with source.open_file('r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"Failed to load JSON from {source.path}: {e}")
    return None


def _write_pattern(file_path: str, pat: dict, workspace: Workspace) -> None:
    """写入单个 pattern 实体"""
    entity_name = pattern_entity_name(pat["name"])
    json_path = pat["name"]
    props = {
        "labels": ["pattern"],
        "name": entity_name,
        "json_path": json_path,
        "type": pat["type"],
        "pattern": pat["pattern"],
    }

    existing = workspace.cypher(
        "MATCH (f:file)--(p:pattern) "
        "WHERE (f.path = $file_path OR f.name = $file_path) "
        "AND p.json_path = $json_path "
        "RETURN p LIMIT 1",
        params={"file_path": file_path, "json_path": json_path},
    )
    if existing:
        workspace.cypher(
            "MATCH (f:file)--(p:pattern) "
            "WHERE (f.path = $file_path OR f.name = $file_path) "
            "AND p.json_path = $json_path "
            "SET p += $props "
            "RETURN p",
            params={"file_path": file_path, "json_path": json_path, "props": props},
        )
        return

    workspace.cypher(
        "MATCH (f:file) "
        "WHERE f.path = $file_path OR f.name = $file_path "
        "CREATE (p:pattern {name: $name}) "
        "SET p.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
        "SET p += $props "
        "MERGE (f)-[:RELATED_TO]->(p) "
        "RETURN p",
        params={
            "file_path": file_path,
            "name": entity_name,
            "props": props,
        },
    )
