"""Serialized Basic Generator - 序列化文件结构分析

职责：
1. 通过 Cypher 发现所有序列化文件
2. 分析文件结构，写入属性时自动实体化
"""
import os
import logging
from datetime import datetime
from storage.workspace import Workspace

logger = logging.getLogger(__name__)


def _discover_serialized_files(workspace: Workspace) -> list:
    """通过统一索引发现所有序列化文件（含虚拟实体）。"""
    exts = ('.json', '.jsonl', '.yaml', '.yml', '.xml', '.toml', '.hcl')
    results = []
    seen = set()
    rows = workspace.cypher("MATCH (n) RETURN n")
    for row in rows:
        props = row.get("n", {})
        name = props.get("name", "")
        if not any(name.endswith(ext) for ext in exts):
            continue
        rel_path = props.get("path", name)
        if rel_path not in seen:
            seen.add(rel_path)
            results.append(rel_path)
    return results


def generate(workspace: Workspace) -> None:
    """发现所有序列化文件，分析结构并写入属性"""
    logger.info("=== Generating serialized file entities ===")

    count = 0
    for path in _discover_serialized_files(workspace):
        try:
            _process_serialized(path, workspace)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to process {path}: {e}")

    logger.info(f"  Processed {count} serialized files")


def _process_serialized(rel_path: str, workspace: Workspace) -> None:
    """处理单个序列化文件：写入结构属性（自动实体化）"""
    if not workspace.data_exists(rel_path):
        return

    basename = os.path.basename(rel_path)
    ext = os.path.splitext(rel_path)[1].lower()
    label_map = {
        ".json": ["file", "json"], ".jsonl": ["file", "json"],
        ".yaml": ["file", "yaml"], ".yml": ["file", "yaml"],
        ".xml": ["file", "xml"], ".toml": ["file", "toml"], ".hcl": ["file", "hcl"],
    }
    labels = label_map.get(ext, ["file", "json"])

    # 分析结构
    abs_path = workspace.resolve_data_path(rel_path)
    with workspace.open_file(rel_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    line_count = len(content.splitlines())
    top_info = _analyze_structure(abs_path, content, rel_path)

    workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": rel_path, "props": {
        "line_count": line_count,
        "char_count": len(content),
        "path": rel_path,
        **top_info,
    }})

    logger.info(f"  Entity: {rel_path} ({top_info.get('structure_type', '?')})")


def _analyze_structure(file_path: str, content: str, path: str) -> dict:
    """分析文件顶层结构"""
    suffix = os.path.splitext(path)[1].lower()

    if suffix in ('.yml',):
        file_type = 'YAML'
    else:
        file_type = suffix.lstrip('.').upper()

    if file_type == 'JSON':
        import json
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return {
                    "structure_type": "object",
                    "top_level_keys": list(data.keys())[:20],
                    "key_count": len(data),
                }
            elif isinstance(data, list):
                return {
                    "structure_type": "array",
                    "array_length": len(data),
                }
            return {"structure_type": type(data).__name__}
        except Exception:
            return {"structure_type": "invalid_json"}

    elif file_type == 'YAML':
        import yaml
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                return {
                    "structure_type": "mapping",
                    "top_level_keys": list(data.keys())[:20],
                    "key_count": len(data),
                }
            elif isinstance(data, list):
                return {"structure_type": "sequence", "sequence_length": len(data)}
            return {"structure_type": type(data).__name__}
        except Exception:
            return {"structure_type": "invalid_yaml"}

    elif file_type == 'XML':
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            root_tag = root.tag.split('}')[-1] if '}' in root.tag else root.tag
            child_tags = list(set(
                child.tag.split('}')[-1] if '}' in child.tag else child.tag
                for child in root
            ))[:20]
            return {"structure_type": "xml", "root_element": root_tag, "child_elements": child_tags}
        except Exception:
            return {"structure_type": "invalid_xml"}

    elif file_type == 'TOML':
        import tomllib
        try:
            with open(file_path, 'rb') as f:
                data = tomllib.load(f)
            if isinstance(data, dict):
                return {
                    "structure_type": "table",
                    "top_level_keys": list(data.keys())[:20],
                    "key_count": len(data),
                }
            return {"structure_type": type(data).__name__}
        except Exception:
            return {"structure_type": "invalid_toml"}

    elif file_type == 'HCL':
        return {"structure_type": "hcl", "note": "HCL structure analysis pending"}

    return {"structure_type": "unknown"}
