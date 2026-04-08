"""Main Extractor - 主提取器

职责：
- 聚合所有独立生成器
- 按正确顺序调用
- 不实现具体逻辑，只做编排

Usage:
    from extractor import extract
    extract("./my_data")
"""
import logging
from pathlib import Path

from extractor.utils import VFSStorage, load_config
from extractor.skeleton import generate_skeleton

# ========== 第二阶段：单节点信息生成 ==========

# DB相关
from extractor.db_info import generate as db_info
from extractor.db_table_info import generate as db_table_info
from extractor.db_column_stats import generate as db_column_stats
from extractor.db_column_sample import generate as db_column_sample
from extractor.db_column_topk import generate as db_column_topk

# CSV相关
from extractor.csv_info import generate as csv_info
from extractor.csv_column_stats import generate as csv_column_stats
from extractor.csv_column_sample import generate as csv_column_sample
from extractor.csv_column_topk import generate as csv_column_topk
from extractor.csv_semantic import generate as csv_semantic

# 序列化文件相关 (JSON/YAML/XML/TOML/HCL)
from extractor.serialized_info import generate as serialized_info

# Text文件相关（通用）
from extractor.text_info import generate as text_info
from extractor.txt_chunk import generate as txt_chunk

# ========== 第三阶段：跨节点 + 语义生成 ==========

# DB关系与语义
from extractor.db_table_relations import generate as db_table_relations
from extractor.db_table_semantic import generate as db_table_semantic
from extractor.db_column_semantic import generate as db_column_semantic

# 序列化文件语义（仅导入存在的模块）
from extractor.json_semantic import generate as json_semantic

# 文档语义
from extractor.md_semantic import generate as md_semantic
from extractor.txt_semantic import generate as txt_semantic

logger = logging.getLogger(__name__)


def extract(target: str, config_path: str = None, verbose: bool = False) -> None:
    """主提取流程 - 仅编排各独立生成器"""

    # 设置日志
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s' if not verbose else '%(levelname)s: %(message)s'
    )

    target_path = Path(target).resolve()
    if not target_path.exists():
        raise ValueError(f"Target path does not exist: {target_path}")

    pontis_path = target_path / ".pontis"

    # 加载配置
    config = load_config(config_path)

    # 初始化存储
    storage = VFSStorage(str(pontis_path))

    logger.info(f"=== Pontis Extractor: {target_path} ===\n")

    # ========== Phase 1: 骨架生成 ==========
    logger.info("[Phase 1] Generating skeleton...")
    generate_skeleton(str(target_path), storage, config)
    logger.info("")

    # ========== Phase 2: DB信息 ==========
    logger.info("[Phase 2] Generating DB info...")
    db_info(storage)
    db_table_info(storage)
    db_column_stats(storage)
    db_column_sample(storage)
    db_column_topk(storage)
    logger.info("")

    # ========== Phase 3: CSV信息 ==========
    logger.info("[Phase 3] Generating CSV info...")
    csv_info(storage)
    csv_column_stats(storage)
    csv_column_sample(storage)
    csv_column_topk(storage)
    logger.info("")

    # ========== Phase 4: 序列化文件信息 ==========
    logger.info("[Phase 4] Generating serialized file info...")
    serialized_info(storage)
    logger.info("")

    # ========== Phase 5: 文本文件信息 ==========
    logger.info("[Phase 5] Generating text file info...")
    text_info(storage)
    logger.info("")

    # ========== Phase 6: Text分片 ==========
    logger.info("[Phase 6] Generating Text chunks...")
    txt_chunk(storage)
    logger.info("")

    # ========== Phase 7: 跨节点关系 ==========
    logger.info("[Phase 7] Generating table relations...")
    db_table_relations(storage)
    logger.info("")

    # ========== Phase 8: 语义分析（AI） ==========
    logger.info("[Phase 8] Generating semantics...")
    db_table_semantic(storage)
    db_column_semantic(storage)
    csv_semantic(storage)
    json_semantic(storage)
    md_semantic(storage)
    txt_semantic(storage)
    logger.info("")

    logger.info("=== Extraction complete ===")
