#!/usr/bin/env python3
"""BIRD 数据库批量提取脚本

对 example_data/bird/dev_databases/ 下的每个数据库单独运行提取。
使用 sketch 模式替代完整统计，适合大表。

两阶段执行：
  阶段一：静态提取（快速，无 AI）
  阶段二：agent_analyze（AI 关系发现 + 总结，详细日志）

日志：每个数据库的 .pontis/extract.log，终端只显示摘要。

Usage:
    python -m extractor.batch_bird
    python -m extractor.batch_bird --no-ai        # 只做静态提取
    python -m extractor.batch_bird --static-only   # 同 --no-ai
    python -m extractor.batch_bird --ai-only       # 只跑 AI 阶段（跳过静态）
"""
import logging
import sys
import time
from pathlib import Path

from extractor.engine import run_pipeline, init_store, get_registry

logger = logging.getLogger(__name__)

# ── 阶段一：静态提取 ──
STATIC_PIPELINE = [
    "db_basic",
    "csv_basic",                    # BIRD: database_description/*.csv
    "db_info",
    "db_table_info",
    "db_column_sketch_stats",
    "csv_info",                     # CSV 列统计
    "db_table_relations",
    "db_column_sketch_overlap",
]

# ── 阶段二：AI 列总结（并行，prompt caching）──
AI_COLUMN_MODULE = "ai_db_column_summary"

# ── 阶段三：Agent 分析 ──
AI_MODULE = "agent_analyze"

# ── 日志格式 ──
_CONSOLE_FMT = "%(asctime)s %(levelname)-5s | %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
_LOG_DATE = "%H:%M:%S"


def _setup_logging(log_path: str = None, verbose: bool = False):
    """配置日志：终端摘要 + 可选文件详细记录。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # 屏蔽 httpx 请求日志
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # 终端：简洁
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, _LOG_DATE))
    root.addHandler(console)

    # 文件：详细
    if log_path:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FILE_FMT, _LOG_DATE))
        root.addHandler(fh)
        return fh
    return None


def _teardown_logging(file_handler):
    """移除文件 handler，准备切换到下一个数据库。"""
    if file_handler is None:
        return
    logging.getLogger().removeHandler(file_handler)
    file_handler.close()


def _run_static(store, config) -> float:
    """阶段一：静态提取，返回耗时秒数。"""
    t0 = time.time()
    run_pipeline(STATIC_PIPELINE, store, config)
    return time.time() - t0


def _run_ai_columns(store, config) -> float:
    """阶段二：AI 列总结（并行），返回耗时秒数。"""
    t0 = time.time()
    registry = get_registry()
    if AI_COLUMN_MODULE not in registry:
        return 0.0
    registry[AI_COLUMN_MODULE](store, config=config)
    return time.time() - t0


def _run_agent(store, *, debug: bool = False) -> float:
    """阶段二：AI 分析（agent_analyze），返回耗时秒数。"""
    registry = get_registry()
    if AI_MODULE not in registry:
        logger.warning(f"Module '{AI_MODULE}' not available, skipping AI phase")
        return 0.0

    t0 = time.time()
    registry[AI_MODULE](store, debug=debug)
    return time.time() - t0


def main():
    args = set(sys.argv[1:])
    no_ai = "--no-ai" in args or "--static-only" in args
    ai_only = "--ai-only" in args
    verbose = "--verbose" in args or "-v" in args
    debug = "--debug" in args

    # 先配一个基础终端日志（main 阶段还没 init_store）
    logging.basicConfig(level=logging.INFO, format=_CONSOLE_FMT, datefmt=_LOG_DATE)

    base = Path(__file__).resolve().parent.parent / "example_data" / "bird" / "dev_databases"
    if not base.exists():
        print(f"Error: {base} does not exist")
        sys.exit(1)

    db_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    if not db_dirs:
        print(f"No database directories found in {base}")
        sys.exit(1)

    mode = "static only" if no_ai else ("AI only" if ai_only else "static + AI")
    print(f"=== BIRD Batch Extract ({mode}) ===")
    print(f"Databases: {len(db_dirs)}\n")

    success, failed = [], []
    total_static = total_ai_col = total_agent = 0.0

    for i, db_dir in enumerate(db_dirs, 1):
        name = db_dir.name
        print(f"[{i}/{len(db_dirs)}] {name}")

        try:
            store, config = init_store(str(db_dir), verbose=False)

            # 确保目录存在再写日志
            pontis_dir = Path(store.project_path) / ".pontis"
            pontis_dir.mkdir(exist_ok=True)
            (pontis_dir / "nodes").mkdir(exist_ok=True)

            # 为这个数据库配置文件日志 → .pontis/extract.log
            log_path = str(pontis_dir / "extract.log")
            fh = _setup_logging(log_path, verbose)

            logger.info(f"=== {name} ===")

            # 阶段一：静态提取
            if not ai_only:
                dt = _run_static(store, config)
                total_static += dt
                print(f"  Static:     {dt:.1f}s")
                logger.info(f"Static phase done: {dt:.1f}s")

            # 阶段二：AI 列总结（并行）
            if not no_ai:
                dt = _run_ai_columns(store, config)
                total_ai_col += dt
                print(f"  AI Columns: {dt:.1f}s")
                logger.info(f"AI columns phase done: {dt:.1f}s")

                # 阶段三：Agent 分析
                dt = _run_agent(store, debug=debug)
                total_agent += dt
                print(f"  Agent:      {dt:.1f}s")
                logger.info(f"Agent phase done: {dt:.1f}s")

            success.append(name)
            logger.info(f"=== {name} done ===")

            # 切换到下一个数据库前移除文件 handler
            _teardown_logging(fh)

        except Exception as e:
            logger.error(f"Failed: {name}: {e}", exc_info=verbose)
            failed.append(name)
            _teardown_logging(locals().get('fh'))

        print()

    # 汇总
    print("=" * 40)
    print(f"Done: {len(success)} ok, {len(failed)} failed")
    print(f"Time: static {total_static:.1f}s, AI cols {total_ai_col:.1f}s, "
          f"agent {total_agent:.1f}s, "
          f"total {total_static + total_ai_col + total_agent:.1f}s")
    if failed:
        print(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
