"""Full Extract — 全量提取管线

编辑下方的 PIPELINE 列表来控制执行哪些模块、按什么顺序。
每个元素是一个模块名，对应 extractor/modules/ 下的一个 generate() 函数。

要跳过某个阶段：直接注释掉对应行。

Usage:
    from extractor.full_extract import extract
    extract("./my_data")
"""
import logging
from typing import List

from extractor.engine import run_pipeline, init_store

logger = logging.getLogger(__name__)


# ╔══════════════════════════════════════════════════════════════════╗
# ║  PIPELINE — 编辑这个列表来控制提取流程                          ║
# ║  注释掉 = 跳过，调换顺序 = 改变执行顺序                        ║
# ╚══════════════════════════════════════════════════════════════════╝

PIPELINE: List[str] = [
    # ── Phase 1: 实体展开（文件节点惰性实体化） ──
    "db_basic",
    "csv_basic",
    "serialized_basic",

    # ── Phase 2: DB 列信息 ──
    "db_column_stats",
    "db_column_sample",
    "db_column_topk",
    # "db_column_sketch_stats",

    # ── Phase 3: CSV 信息 ──
    "csv_info",
    "csv_column_stats",
    "csv_column_sample",
    "csv_column_topk",

    # ── Phase 4: 序列化文件 ──
    "json_pattern",

    # ── Phase 5: 文本文件 ──
    "text_info",

    # ── Phase 6: 关系检测 ──
    "db_table_relations",
    "db_column_overlap",

    # ── Phase 7: AI 总结（仅列级，库/表级由 agent_summary 完成） ──
    "ai_db_column_summary",
    "ai_json_summary",
    "ai_text_summary",
]


def extract(target: str, config_path: str = None, verbose: bool = False) -> None:
    """全量提取入口"""
    store, config = init_store(target, config_path, verbose)

    logger.info(f"=== Pontis Extractor: {store.project_path} ===")
    logger.info(f"Pipeline: {len(PIPELINE)} modules\n")

    run_pipeline(PIPELINE, store, config)

    logger.info("\n=== Extraction complete ===")
