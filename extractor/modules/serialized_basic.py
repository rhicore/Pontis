"""Serialized Basic Generator - 序列化文件实体展开器

职责：
- 匹配 *.json / *.yaml / *.xml / *.toml / *.hcl 节点
- 分析文件结构（object/array/mapping 等）
- 更新 _meta.yml（不含 _raw 缓存）
- 创建 _entity/ 目录

独立执行：
    python -m extractor.serialized_basic ./my_data
"""
import os
import logging
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有序列化文件节点展开实体"""
    logger.info("=== Generating serialized file entities ===")

    for pattern in ["*.json", "*.yaml", "*.xml", "*.toml", "*.hcl"]:
        for path in store.find_nodes(pattern):
            try:
                _expand_serialized(path, store)
            except Exception as e:
                logger.warning(f"Failed to expand {path}: {e}")


def _expand_serialized(path: str, store: Store) -> None:
    """分析序列化文件结构，更新 _meta.yml，创建 _entity/"""
    meta = store.get_meta(path)
    if not meta:
        return

    if "structure_type" in meta:
        return

    rel_path = meta.get("path")
    file_path = os.path.join(store.project_path, rel_path) if rel_path else None
    if not file_path or not os.path.exists(file_path):
        return

    try:
        stat = os.stat(file_path)
        file_size = stat.st_size

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        line_count = len(content.splitlines())
        top_info = _analyze_structure(file_path, content, path)

        store.set_meta(path, {
            "file_size": file_size,
            "line_count": line_count,
            "char_count": len(content),
            **top_info,
        })

        logger.info(f"  Entity: {path} ({top_info.get('structure_type', '?')})")

    except Exception as e:
        logger.debug(f"Could not expand {path}: {e}")


def _analyze_structure(file_path: str, content: str, path: str) -> dict:
    """分析文件顶层结构"""
    # 从路径推断文件类型
    basename = os.path.basename(path)
    suffix = os.path.splitext(basename)[1] if '.' in basename else ''

    if suffix in ('.yml',):
        file_type = 'YAML'
    else:
        file_type = (suffix or '').lstrip('.').upper()

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
