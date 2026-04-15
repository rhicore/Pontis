"""Text Basic Generator - 文本文件发现与节点创建

职责：
1. 通过 store.find_nodes() 发现所有文本文件（含虚节点）
2. 为未索引的文件创建节点（含 _inode）

后续的 text_info 模块负责补充统计信息。
"""
import os
import logging
from datetime import datetime
from storage import Store

logger = logging.getLogger(__name__)

# 文本文件后缀（与 text_info.py 保持一致）
TEXT_EXTENSIONS = {
    '.txt', '.log', '.text',
    '.md', '.markdown',
    '.py', '.pyw', '.pyi',
    '.js', '.mjs', '.cjs',
    '.ts', '.tsx', '.jsx',
    '.java',
    '.c', '.h',
    '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx',
    '.go',
    '.rs',
    '.rb', '.erb',
    '.php', '.phtml',
    '.swift',
    '.kt', '.kts',
    '.scala', '.sc',
    '.r', '.rmd',
    '.pl', '.pm',
    '.lua',
    '.sh', '.bash', '.zsh',
    '.ps1',
    '.bat', '.cmd',
    '.html', '.htm',
    '.css', '.scss', '.sass', '.less',
    '.sql', '.ddl', '.dml',
    '.rst', '.adoc',
    '.tex', '.bib',
    '.ini', '.cfg', '.conf', '.config',
    '.properties',
    '.env',
}


def generate(store: Store) -> None:
    """发现所有文本文件并创建文件节点"""
    logger.info("=== Generating Text entities ===")

    count = 0
    for ext in TEXT_EXTENSIONS:
        for path in store.find_nodes(f"**/*{ext}"):
            if store.node_exists(path):
                continue
            abs_path = os.path.join(store.project_path, path)
            if not os.path.exists(abs_path):
                continue

            stat = os.stat(abs_path)
            meta = {
                "path": path,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "created_at": datetime.now().isoformat(),
            }
            store.create_node(path, meta=meta)
            logger.info(f"  Created file node: {path}")
            count += 1

    logger.info(f"  Processed {count} new text files")
