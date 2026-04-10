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

# ========== Phase 1.5: 实体展开 ==========

from extractor.db_basic import generate as db_basic
from extractor.csv_basic import generate as csv_basic
from extractor.serialized_basic import generate as serialized_basic
from extractor.text_basic import generate as text_basic

# ========== Phase 2: 单节点信息生成 ==========

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

# 序列化文件相关
from extractor.json_pattern import generate as json_pattern

# Text文件相关
from extractor.text_info import generate as text_info

# ========== Phase 3: 跨节点 + 语义生成 ==========

# DB关系
from extractor.db_table_relations import generate as db_table_relations
from extractor.db_column_overlap import generate as db_column_overlap
from extractor.db_column_rel import generate as db_column_rel

# AI 总结
from extractor.ai_db_summary import generate as ai_db_summary
from extractor.ai_db_table_summary import generate as ai_db_table_summary
from extractor.ai_db_column_summary import generate as ai_db_column_summary
from extractor.ai_json_summary import generate as ai_json_summary
from extractor.ai_text_summary import generate as ai_text_summary

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

    # 清空旧边（每次全量重建）
    storage.write_edges([])

    logger.info(f"=== Pontis Extractor: {target_path} ===\n")

    # ========== Phase 1: 骨架生成 ==========
    logger.info("[Phase 1] Generating skeleton...")
    generate_skeleton(str(target_path), storage, config)
    logger.info("")

    # ========== Phase 1.5: 实体展开 ==========
    logger.info("[Phase 1.5] Expanding entities...")
    db_basic(storage)
    csv_basic(storage)
    serialized_basic(storage)
    text_basic(storage)
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
    json_pattern(storage)
    logger.info("")

    # ========== Phase 5: 文本文件信息 ==========
    logger.info("[Phase 5] Generating text file info...")
    text_info(storage)
    logger.info("")

    # ========== Phase 6: 跨节点关系 ==========
    logger.info("[Phase 6] Generating table relations...")
    db_table_relations(storage)
    logger.info("")

    # ========== Phase 7: 列值重叠检测 ==========
    logger.info("[Phase 7] Detecting column overlaps...")
    db_column_overlap(storage, config)
    logger.info("")

    # ========== Phase 8: 列关系打分 ==========
    logger.info("[Phase 8] Scoring column relations...")
    db_column_rel(storage, config)
    logger.info("")

    # ========== Phase 9: AI 总结 ==========
    logger.info("[Phase 9] AI summaries...")
    ai_db_summary(storage)
    ai_db_table_summary(storage)
    ai_db_column_summary(storage)
    ai_json_summary(storage)
    ai_text_summary(storage)
    logger.info("")

    logger.info("=== Extraction complete ===")
