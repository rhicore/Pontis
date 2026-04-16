#!/usr/bin/env python3
"""BIRD 数据库批量提取脚本

对 example_data/bird/dev_databases/ 下的每个数据库单独运行提取。
使用 sketch 模式替代完整统计，适合大表。

Usage:
    python -m extractor.batch_bird
    python -m extractor.batch_bird --no-ai        # 跳过 AI 总结
"""
import logging
import sys
from pathlib import Path

from extractor.engine import run_pipeline, init_store, get_registry

logger = logging.getLogger(__name__)

# sketch 模式 pipeline：db_column_sketch_stats 替代 stats+sample+topk
SKETCH_PIPELINE = [
    "db_basic",
    "db_info",
    "db_table_info",
    "db_column_sketch_stats",
    "db_table_relations",
    "db_column_overlap",
    "db_column_rel",
    "db_column_lsh_index",
]

SKETCH_PIPELINE_AI = SKETCH_PIPELINE + [
    "ai_db_summary",
    "ai_db_table_summary",
    "ai_db_column_summary",
]


def main():
    no_ai = "--no-ai" in sys.argv
    pipeline = SKETCH_PIPELINE if no_ai else SKETCH_PIPELINE_AI

    base = Path(__file__).parent.parent / "example_data" / "bird" / "dev_databases"
    if not base.exists():
        print(f"Error: {base} does not exist")
        sys.exit(1)

    # 发现所有数据库子目录
    db_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    if not db_dirs:
        print(f"No database directories found in {base}")
        sys.exit(1)

    print(f"=== BIRD Batch Extract ===")
    print(f"Databases: {len(db_dirs)}")
    print(f"Pipeline: {pipeline}\n")

    success, failed = [], []

    for i, db_dir in enumerate(db_dirs, 1):
        name = db_dir.name
        print(f"[{i}/{len(db_dirs)}] {name}")
        try:
            store, config = init_store(str(db_dir), verbose=False)
            store.clear_edges()
            run_pipeline(pipeline, store, config)
            success.append(name)
        except Exception as e:
            logger.error(f"Failed: {name}: {e}")
            failed.append(name)
        print()

    print(f"=== Done: {len(success)} succeeded, {len(failed)} failed ===")
    if failed:
        print(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
