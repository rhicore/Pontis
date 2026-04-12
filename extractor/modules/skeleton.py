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
from storage import Store
from extractor.utils import Config, load_config

logger = logging.getLogger(__name__)


def generate_skeleton(source_path: str, store: Store, config: Config) -> None:
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
            _sync_file(physical_path, rel_root, store)

        # 处理目录（如果需要为目录创建节点）
        for name in dirs:
            if name.startswith('.') or name.startswith('_'):
                continue
            if name == config.pontis_dir_name:
                continue


def _sync_file(physical_path: str, parent_rel_path: str, store: Store) -> None:
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

    store.create_node(rel_path, meta=meta)
    logger.info(f"  Created skeleton: {rel_path}")

    # 实体展开由各 [type]_basic.py 处理，此处不展开
