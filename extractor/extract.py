"""Main Extractor - 配置驱动的提取管线

执行 extractor/pipeline.py 中定义的 PIPELINE 列表。
要调整执行哪些模块，直接编辑 pipeline.py 中的 PIPELINE 列表。

Usage:
    from extractor import extract
    extract("./my_data")
"""
import logging
from pathlib import Path

from extractor.utils import VFSStorage, load_config
from extractor.pipeline import PIPELINE, run_pipeline

logger = logging.getLogger(__name__)


def extract(target: str, config_path: str = None, verbose: bool = False) -> None:
    """主提取流程"""

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s' if not verbose else '%(levelname)s: %(message)s'
    )

    target_path = Path(target).resolve()
    if not target_path.exists():
        raise ValueError(f"Target path does not exist: {target_path}")

    pontis_path = target_path / ".pontis"
    config = load_config(config_path)
    storage = VFSStorage(str(pontis_path))

    # 清空旧边（每次全量重建）
    storage.write_edges([])

    logger.info(f"=== Pontis Extractor: {target_path} ===")
    logger.info(f"Pipeline: {len(PIPELINE)} modules\n")

    # 执行 pipeline
    run_pipeline(PIPELINE, storage, str(target_path), config)

    logger.info("\n=== Extraction complete ===")
