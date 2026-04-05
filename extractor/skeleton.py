"""Skeleton Generator - 骨架生成器

职责：
1. 遍历源文件夹
2. 生成VFS文件树结构
3. 生成最基础的_meta.yml（仅包含：path/修改时间/生成时间）

不生成：
- 统计数据
- 关系信息
- AI总结
- 任何嵌套列表
"""
import os
import logging
from datetime import datetime
from extractor.utils import VFSStorage, NodeRef, Config, load_config

logger = logging.getLogger(__name__)


def generate_skeleton(source_path: str, storage: VFSStorage, config: Config) -> None:
    """生成VFS文件树骨架

    遍历源文件夹，为每个文件/目录创建对应VFS节点
    仅生成基础_meta.yml
    """
    logger.info("=== Generating skeleton ===")

    for root, dirs, files in os.walk(source_path):
        # 跳过pontis目录
        if config.pontis_dir_name in root:
            continue

        rel_root = os.path.relpath(root, source_path)
        if rel_root == '.':
            rel_root = ''

        # 处理文件
        for name in files:
            if name.startswith('.') or name.startswith('_'):
                continue
            if name == config.pontis_dir_name:
                continue

            physical_path = os.path.join(root, name)
            _sync_file(physical_path, rel_root, storage)

        # 处理目录（如果需要为目录创建节点）
        for name in dirs:
            if name.startswith('.') or name.startswith('_'):
                continue
            if name == config.pontis_dir_name:
                continue


def _sync_file(physical_path: str, parent_rel_path: str, storage: VFSStorage) -> None:
    """同步单个文件到VFS"""
    # 检查文件是否存在（可能在遍历后被删除，如SQLite临时文件）
    if not os.path.exists(physical_path):
        return

    ext = os.path.splitext(physical_path)[1].lower()
    basename = os.path.basename(physical_path)
    basename_lower = basename.lower()
    name_without_ext = os.path.splitext(basename)[0]

    # 构建节点名称
    suffix_map = {
        '.sqlite': '.db', '.sqlite3': '.db', '.db': '.db', '.duckdb': '.db',
        '.csv': '.csv', '.tsv': '.tsv',
        '.json': '.json', '.jsonl': '.json',
        '.md': '.md', '.markdown': '.md',
        '.txt': '.txt', '.log': '.txt', '.text': '.txt',
        '.pdf': '.pdf',
        '.yaml': '.yaml', '.yml': '.yaml',
        '.xml': '.xml', '.xsd': '.xml', '.xslt': '.xml', '.svg': '.xml',
        '.toml': '.toml',
        '.hcl': '.hcl',
        '.ini': '.ini', '.cfg': '.ini', '.conf': '.ini', '.config': '.ini',
        '.properties': '.properties',
        '.sql': '.sql', '.ddl': '.sql', '.dml': '.sql',
        '.py': '.py', '.pyw': '.py', '.pyi': '.py',
        '.js': '.js', '.mjs': '.js', '.cjs': '.js',
        '.ts': '.ts', '.tsx': '.tsx',
        '.jsx': '.jsx',
        '.java': '.java',
        '.c': '.c', '.h': '.h',
        '.cpp': '.cpp', '.hpp': '.cpp', '.cc': '.cpp', '.hh': '.cpp', '.cxx': '.cpp', '.hxx': '.cpp',
        '.go': '.go',
        '.rs': '.rs',
        '.rb': '.rb', '.erb': '.rb',
        '.php': '.php', '.phtml': '.php',
        '.swift': '.swift',
        '.kt': '.kt', '.kts': '.kt',
        '.scala': '.scala', '.sc': '.scala',
        '.r': '.r', '.rmd': '.r',
        '.pl': '.pl', '.pm': '.pl',
        '.lua': '.lua',
        '.sh': '.sh', '.bash': '.sh', '.zsh': '.sh', '.fish': '.sh',
        '.ps1': '.ps1', '.psm1': '.ps1', '.psd1': '.ps1',
        '.bat': '.bat', '.cmd': '.bat',
        '.html': '.html', '.htm': '.html', '.xhtml': '.html',
        '.css': '.css', '.scss': '.css', '.sass': '.css', '.less': '.css',
        '.rst': '.rst', '.adoc': '.adoc', '.asciidoc': '.adoc',
        '.tex': '.tex', '.latex': '.tex', '.bib': '.bib',
    }

    # 特殊文件名处理
    special_names = {
        'makefile': '.mk', 'rakefile': '.rb', 'gemfile': '.rb',
        'dockerfile': '.dockerfile', 'jenkinsfile': '.jenkinsfile',
        'vagrantfile': '.rb', 'brewfile': '.rb',
        'cmakelists.txt': '.cmake',
        '.gitignore': '.gitignore', '.gitattributes': '.gitattributes',
        '.dockerignore': '.dockerignore', '.editorconfig': '.editorconfig',
        '.env': '.env', '.envrc': '.env',
        'readme': '.md', 'license': '.txt', 'copying': '.txt',
        'changelog': '.md', 'changes': '.md', 'news': '.md', 'history': '.md',
    }

    if basename_lower in special_names:
        suffix = special_names[basename_lower]
        node_name = f"{name_without_ext}{suffix}"
    else:
        suffix = suffix_map.get(ext, ext)
        node_name = f"{name_without_ext}{suffix}"

    # 确定节点类型（从suffix映射）
    node_type_map = {
        '.db': 'DB',
        '.csv': 'CSV',
        '.tsv': 'TSV',
        '.json': 'JSON',
        '.yaml': 'YAML',
        '.xml': 'XML',
        '.toml': 'TOML',
        '.hcl': 'HCL',
        '.md': 'Markdown',
        '.txt': 'Text',
        '.ini': 'Text',
        '.properties': 'Text',
        '.sql': 'Text',
        '.py': 'Text',
        '.js': 'Text',
        '.ts': 'Text',
        '.tsx': 'Text',
        '.jsx': 'Text',
        '.java': 'Text',
        '.c': 'Text',
        '.h': 'Text',
        '.cpp': 'Text',
        '.go': 'Text',
        '.rs': 'Text',
        '.rb': 'Text',
        '.php': 'Text',
        '.swift': 'Text',
        '.kt': 'Text',
        '.scala': 'Text',
        '.r': 'Text',
        '.pl': 'Text',
        '.lua': 'Text',
        '.sh': 'Text',
        '.ps1': 'Text',
        '.bat': 'Text',
        '.html': 'Text',
        '.css': 'Text',
        '.rst': 'Text',
        '.adoc': 'Text',
        '.tex': 'Text',
        '.dockerfile': 'Text',
        '.jenkinsfile': 'Text',
        '.cmake': 'Text',
        '.gitignore': 'Text',
        '.env': 'Text',
    }
    node_type = node_type_map.get(suffix, 'UNKNOWN')

    # 创建节点
    rel_path = os.path.join(parent_rel_path, node_name) if parent_rel_path else node_name
    node = NodeRef(rel_path, storage.pontis_root)

    # 获取文件统计信息
    stat = os.stat(physical_path)
    modified_time = datetime.fromtimestamp(stat.st_mtime).isoformat()

    # 写入基础_meta.yml（仅包含最基本信息）
    # 注意：name可以从文件夹名解析，不需要存储；suffix/type可以从后缀推断
    # path 是相对路径（相对于项目根目录）
    rel_source_path = os.path.join(parent_rel_path, basename) if parent_rel_path else basename
    meta = {
        "path": rel_source_path,
        "modified_at": modified_time,
        "created_at": datetime.now().isoformat(),
    }

    storage.ensure_dir(node.full_path)
    storage.write_meta(node, meta)
    logger.info(f"  Created skeleton: {rel_path}")

    # 根据类型展开子结构
    if node_type == 'DB':
        _expand_database(physical_path, node, storage)
    elif node_type == 'CSV':
        _expand_csv(physical_path, node, storage, delimiter=',')
    elif node_type == 'TSV':
        _expand_csv(physical_path, node, storage, delimiter='\t')
    elif node_type in ['JSON', 'YAML', 'XML', 'TOML', 'HCL', 'Markdown']:
        _expand_serialized_file(physical_path, node, storage, node_type)


def _expand_database(db_path: str, db_node: NodeRef, storage: VFSStorage) -> None:
    """展开数据库为表和列结构

    仅创建文件夹结构，不添加统计信息
    """
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 获取表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cursor.fetchall()

        for (table_name,) in tables:
            # 创建表节点
            safe_name = table_name.replace("/", "_").replace("\\", "_")
            table_node_name = f"{safe_name}.table"
            table_rel_path = os.path.join(db_node.rel_path, table_node_name)
            table_node = NodeRef(table_rel_path, storage.pontis_root)

            # 基础表meta - 只保留created_at（其他信息可以从路径推断）
            table_meta = {
                "created_at": datetime.now().isoformat(),
            }
            storage.ensure_dir(table_node.full_path)
            storage.write_meta(table_node, table_meta)
            logger.info(f"    Created: {db_node.name}/{table_node_name}")

            # 获取列并创建列节点
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            columns = cursor.fetchall()

            for col in columns:
                col_name = col[1]
                col_type = _normalize_type(col[2])

                safe_col_name = col_name.replace("/", "_").replace("\\", "_")
                col_node_name = f"{safe_col_name}.{col_type}.col"
                col_rel_path = os.path.join(table_node.rel_path, col_node_name)
                col_node = NodeRef(col_rel_path, storage.pontis_root)

                # 基础列meta - 只保留created_at
                col_meta = {
                    "created_at": datetime.now().isoformat(),
                }
                storage.ensure_dir(col_node.full_path)
                storage.write_meta(col_node, col_meta)

        # 获取视图
        cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
        views = cursor.fetchall()

        for (view_name,) in views:
            safe_name = view_name.replace("/", "_").replace("\\", "_")
            view_node_name = f"{safe_name}.view"
            view_rel_path = os.path.join(db_node.rel_path, view_node_name)
            view_node = NodeRef(view_rel_path, storage.pontis_root)

            view_meta = {
                "created_at": datetime.now().isoformat(),
            }
            storage.ensure_dir(view_node.full_path)
            storage.write_meta(view_node, view_meta)
            logger.info(f"    Created: {db_node.name}/{view_node_name}")

        conn.close()
    except Exception as e:
        logger.warning(f"Failed to expand database {db_path}: {e}")


def _expand_csv(csv_path: str, csv_node: NodeRef, storage: VFSStorage, delimiter: str = ',') -> None:
    """展开CSV/TSV为列结构"""
    try:
        import csv

        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            # 读取表头
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader, None)

        if not headers:
            return

        # 为每个列创建节点
        for col_name in headers:
            safe_name = col_name.replace("/", "_").replace("\\", "_").replace(".", "_")
            col_node_name = f"{safe_name}.TEXT.col"
            col_rel_path = os.path.join(csv_node.rel_path, col_node_name)
            col_node = NodeRef(col_rel_path, storage.pontis_root)

            col_meta = {
                "created_at": datetime.now().isoformat(),
            }
            storage.ensure_dir(col_node.full_path)
            storage.write_meta(col_node, col_meta)

        logger.info(f"    Created {len(headers)} columns: {csv_node.name}")

    except Exception as e:
        logger.warning(f"Failed to expand CSV {csv_path}: {e}")


def _expand_serialized_file(file_path: str, file_node: NodeRef, storage: VFSStorage, file_type: str) -> None:
    """展开序列化文件为基础结构（无内部嵌套）

    创建：
    - _meta.yml: 记录文件外部信息（大小、行数、顶层结构等）
    - _bin (可选): 文件内容缓存
    """
    try:
        import json
        import yaml

        stat = os.stat(file_path)
        file_size = stat.st_size

        # 读取文件内容用于分析
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        line_count = len(content.splitlines())

        # 解析顶层结构
        top_level_info = {}
        try:
            if file_type == 'JSON':
                data = json.loads(content)
                if isinstance(data, dict):
                    top_level_info = {
                        "structure_type": "object",
                        "top_level_keys": list(data.keys())[:20],  # 限制数量
                        "key_count": len(data)
                    }
                elif isinstance(data, list):
                    top_level_info = {
                        "structure_type": "array",
                        "array_length": len(data)
                    }
                else:
                    top_level_info = {"structure_type": type(data).__name__}

            elif file_type == 'YAML':
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    top_level_info = {
                        "structure_type": "mapping",
                        "top_level_keys": list(data.keys())[:20],
                        "key_count": len(data)
                    }
                elif isinstance(data, list):
                    top_level_info = {
                        "structure_type": "sequence",
                        "sequence_length": len(data)
                    }

            elif file_type == 'XML':
                import xml.etree.ElementTree as ET
                root = ET.fromstring(content)
                top_level_info = {
                    "structure_type": "xml",
                    "root_element": root.tag.split('}')[-1] if '}' in root.tag else root.tag,
                    "child_elements": list(set([child.tag.split('}')[-1] if '}' in child.tag else child.tag
                                                for child in root]))[:20]
                }

            elif file_type == 'TOML':
                import tomllib
                with open(file_path, 'rb') as f:
                    data = tomllib.load(f)
                if isinstance(data, dict):
                    top_level_info = {
                        "structure_type": "table",
                        "top_level_keys": list(data.keys())[:20],
                        "key_count": len(data)
                    }

            elif file_type == 'HCL':
                # HCL 简化处理，只记录基础信息
                top_level_info = {
                    "structure_type": "hcl",
                    "note": "HCL structure analysis pending"
                }

            elif file_type == 'Markdown':
                # Markdown 处理：统计标题数量、代码块等
                import re
                headings = re.findall(r'^#+\s+', content, re.MULTILINE)
                code_blocks = re.findall(r'```[\w]*\n', content)
                links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)

                top_level_info = {
                    "structure_type": "markdown",
                    "heading_count": len(headings),
                    "code_block_count": len(code_blocks),
                    "link_count": len(links),
                }
        except Exception as e:
            top_level_info = {
                "structure_type": "unknown",
                "parse_error": str(e)
            }

        # 更新 meta
        meta = storage.read_meta(file_node)
        if meta:
            meta.update({
                "file_size": file_size,
                "line_count": line_count,
                "char_count": len(content),
                **top_level_info
            })
            storage.write_meta(file_node, meta)

        # 写入 _raw 缓存（如果文件不太大）- 直接存储原始内容
        if file_size < 10 * 1024 * 1024:  # 10MB 限制
            storage.write_text(file_node, content)

        logger.info(f"    Processed {file_type}: {file_node.name} ({line_count} lines, {len(top_level_info.get('top_level_keys', []))} top keys)")

    except Exception as e:
        logger.warning(f"Failed to expand serialized file {file_path}: {e}")


def _normalize_type(sql_type: str) -> str:
    """标准化SQL类型"""
    sql_type_upper = (sql_type or "").upper()

    if any(t in sql_type_upper for t in ['INT', 'SERIAL', 'BIGINT']):
        return "INT"
    elif any(t in sql_type_upper for t in ['REAL', 'FLOAT', 'DOUBLE', 'DECIMAL']):
        return "REAL"
    elif any(t in sql_type_upper for t in ['TEXT', 'CLOB', 'CHAR', 'VARCHAR']):
        return "TEXT"
    elif any(t in sql_type_upper for t in ['BLOB', 'BINARY']):
        return "BLOB"
    elif 'JSON' in sql_type_upper:
        return "JSON"
    elif 'BOOLEAN' in sql_type_upper or 'BOOL' in sql_type_upper:
        return "BOOL"
    elif any(t in sql_type_upper for t in ['DATE', 'TIME']):
        return "DATETIME"
    return "TEXT"


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate VFS skeleton")
    parser.add_argument('target', help='Source directory to scan')
    parser.add_argument('-c', '--config', help='Config file path')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    if not os.path.isdir(target_path):
        print(f"Error: Not a directory: {target_path}", file=sys.stderr)
        sys.exit(1)

    pontis_path = os.path.join(target_path, ".pontis")
    os.makedirs(pontis_path, exist_ok=True)

    config = load_config(args.config)
    storage = VFSStorage(pontis_path)
    generate_skeleton(target_path, storage, config)
    print("Done.")


if __name__ == '__main__':
    main()
