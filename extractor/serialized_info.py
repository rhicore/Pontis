"""Serialized Info Generator - 序列化文件信息生成器

职责：
- 匹配 *.json/*.yaml/*.xml/*.toml/*.hcl 节点
- 添加文件级元信息（大小、行数、顶层结构等）

独立执行：
    python -m extractor.serialized_info ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有序列化文件节点生成信息"""
    logger.info("=== Generating serialized file info ===")

    patterns = ["*.json", "*.yaml", "*.xml", "*.toml", "*.hcl"]

    for pattern in patterns:
        for node in storage.find_nodes(pattern):
            try:
                _generate_for_file(node, storage)
            except Exception as e:
                logger.warning(f"Failed to generate info for {node.name}: {e}")


def _generate_for_file(node: NodeRef, storage: VFSStorage) -> bool:
    """为单个序列化文件生成信息"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    # 跳过已处理的
    if "structure_type" in meta:
        return False

    rel_path = meta.get("path")
    file_path = storage.resolve_path(rel_path) if rel_path else None
    if not file_path or not os.path.exists(file_path):
        return False

    file_type = node.name.split('.')[-1].upper() if '.' in node.name else "Unknown"

    try:
        # 基础文件信息
        stat = os.stat(file_path)
        file_size = stat.st_size

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        line_count = len(content.splitlines())

        # 解析顶层结构
        top_level_info = _analyze_structure(file_path, content, file_type)

        # 更新meta
        meta.update({
            "file_size": file_size,
            "line_count": line_count,
            "char_count": len(content),
            **top_level_info
        })
        storage.write_meta(node, meta)

        # 写入_raw缓存（如果文件不太大）- 直接存储原始内容
        if file_size < 10 * 1024 * 1024:  # 10MB限制
            storage.write_text(node, content)

        logger.info(f"  Info: {node.rel_path} ({line_count} lines, {top_level_info.get('structure_type', 'unknown')})")
        return True

    except Exception as e:
        logger.debug(f"Could not get file info: {e}")
        return False


def _analyze_structure(file_path: str, content: str, file_type: str) -> dict:
    """分析文件顶层结构"""

    if file_type == "JSON":
        import json
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return {
                    "structure_type": "object",
                    "top_level_keys": list(data.keys())[:20],
                    "key_count": len(data)
                }
            elif isinstance(data, list):
                return {
                    "structure_type": "array",
                    "array_length": len(data)
                }
            else:
                return {"structure_type": type(data).__name__}
        except:
            return {"structure_type": "invalid_json"}

    elif file_type == "YAML":
        import yaml
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                return {
                    "structure_type": "mapping",
                    "top_level_keys": list(data.keys())[:20],
                    "key_count": len(data)
                }
            elif isinstance(data, list):
                return {
                    "structure_type": "sequence",
                    "sequence_length": len(data)
                }
            else:
                return {"structure_type": type(data).__name__}
        except:
            return {"structure_type": "invalid_yaml"}

    elif file_type == "XML":
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            root_tag = root.tag.split('}')[-1] if '}' in root.tag else root.tag
            child_tags = list(set([
                child.tag.split('}')[-1] if '}' in child.tag else child.tag
                for child in root
            ]))[:20]
            return {
                "structure_type": "xml",
                "root_element": root_tag,
                "child_elements": child_tags
            }
        except:
            return {"structure_type": "invalid_xml"}

    elif file_type == "TOML":
        import tomllib
        try:
            with open(file_path, 'rb') as f:
                data = tomllib.load(f)
            if isinstance(data, dict):
                return {
                    "structure_type": "table",
                    "top_level_keys": list(data.keys())[:20],
                    "key_count": len(data)
                }
            else:
                return {"structure_type": type(data).__name__}
        except:
            return {"structure_type": "invalid_toml"}

    elif file_type == "HCL":
        # HCL简化处理
        return {
            "structure_type": "hcl",
            "note": "HCL structure analysis pending"
        }

    return {"structure_type": "unknown"}


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate serialized file info")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
