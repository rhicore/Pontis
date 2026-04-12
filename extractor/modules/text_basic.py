"""Text Basic Generator - 文本文件实体展开器

职责：
- 匹配 *.txt 节点
- 创建 _entity/ 目录（预留）

独立执行：
    python -m extractor.text_basic ./my_data
"""
import os
import logging
from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """为所有文本文件节点创建 _entity 目录"""
    logger.info("=== Generating Text entities ===")

    for path in store.find_nodes("*.txt"):
        try:
            logger.info(f"  Entity: {path}")
        except Exception as e:
            logger.warning(f"Failed to create text entity for {path}: {e}")
