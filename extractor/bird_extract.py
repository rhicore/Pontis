#!/usr/bin/env python3
"""BIRD 数据库提取脚本

对 example_data/bird/dev_databases/ 下的数据库运行提取。
使用 sketch 模式替代完整统计，适合大表。

三阶段：
  1. 静态提取（db_basic, csv_basic, stats, relations, overlap...）
  2. AI 列总结（并行 prompt caching）
  3. Agent 分析（关系发现 + 总结生成）

日志：每个数据库的 .pontis/extract.log，终端只显示摘要。

Usage:
    python -m extractor.bird_extract                      # 全量
    python -m extractor.bird_extract --db toxicology       # 指定库
    python -m extractor.bird_extract --force               # 强制重新提取
    python -m extractor.bird_extract --no-ai               # 只做静态提取
    python -m extractor.bird_extract --ai-only             # 只跑 AI 阶段
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
    "db_column_stats",
    "db_column_sample",
    "db_column_topk",
    "csv_info",
    "db_table_relations",
    "db_fk_validate",
    "db_column_overlap",
]

AI_COLUMN_MODULE = "ai_db_column_summary"
AI_MODULE = "agent_analyze"
JOIN_DETECT_MODULE = "agent_join_detect"
DISAMBIGUATE_MODULE = "agent_disambiguate"

# ── 日志格式 ──
_CONSOLE_FMT = "%(asctime)s %(levelname)-5s | %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
_LOG_DATE = "%H:%M:%S"


def _setup_file_log(log_path: str) -> logging.FileHandler:
    """配置文件日志 handler。"""
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FILE_FMT, _LOG_DATE))
    logging.getLogger().addHandler(fh)
    return fh


def _teardown_file_log(fh: logging.FileHandler) -> None:
    """移除文件 handler。"""
    logging.getLogger().removeHandler(fh)
    fh.close()


def extract_one(db_dir: str, force: bool = False, no_ai: bool = False,
                ai_only: bool = False, debug: bool = False) -> dict:
    """提取单个数据库。

    Args:
        db_dir: 数据库目录绝对路径
        force: 强制重新提取（删除旧 .pontis）
        no_ai: 只做静态提取
        ai_only: 只跑 AI 阶段（跳过静态）
        debug: agent debug 模式

    Returns:
        {"name": str, "static": float, "ai_columns": float, "agent": float}
    """
    db_dir = Path(db_dir).resolve()
    name = db_dir.name
    pontis_dir = db_dir / ".pontis"

    # force 模式：删除旧的
    if force and pontis_dir.exists():
        import shutil
        shutil.rmtree(pontis_dir)
        logger.info(f"  已删除旧 .pontis: {name}")

    store, config = init_store(str(db_dir))

    # 确保目录结构
    pontis_dir.mkdir(exist_ok=True)
    (pontis_dir / "nodes").mkdir(exist_ok=True)

    # 文件日志
    fh = _setup_file_log(str(pontis_dir / "extract.log"))

    logger.info(f"=== {name} ===")
    result = {"name": name, "static": 0.0, "ai_columns": 0.0, "agent": 0.0}

    try:
        # 阶段一：静态
        if not ai_only:
            t0 = time.time()
            run_pipeline(STATIC_PIPELINE, store, config)
            dt = time.time() - t0
            result["static"] = dt
            logger.info(f"Static phase done: {dt:.1f}s")

        # 阶段二 + 三：AI
        if not no_ai:
            registry = get_registry()

            # AI 列总结
            if AI_COLUMN_MODULE in registry:
                t0 = time.time()
                registry[AI_COLUMN_MODULE](store, config=config)
                dt = time.time() - t0
                result["ai_columns"] = dt
                logger.info(f"AI columns phase done: {dt:.1f}s")

            # Agent 分析
            if AI_MODULE in registry:
                t0 = time.time()
                registry[AI_MODULE](store, debug=debug)
                dt = time.time() - t0
                result["agent"] = dt
                logger.info(f"Agent phase done: {dt:.1f}s")

            # Agent 关系发现
            if JOIN_DETECT_MODULE in registry:
                t0 = time.time()
                registry[JOIN_DETECT_MODULE](store, debug=debug)
                dt = time.time() - t0
                result["join_detect"] = dt
                logger.info(f"Join detect phase done: {dt:.1f}s")

            # Agent 语义消歧
            if DISAMBIGUATE_MODULE in registry:
                t0 = time.time()
                registry[DISAMBIGUATE_MODULE](store, debug=debug)
                dt = time.time() - t0
                result["disambiguate"] = dt
                logger.info(f"Disambiguate phase done: {dt:.1f}s")

        logger.info(f"=== {name} done ===")

    finally:
        _teardown_file_log(fh)

    return result


def main():
    args = set(sys.argv[1:])
    db_filter = None
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            db_filter = a
            break

    no_ai = "--no-ai" in args or "--static-only" in args
    ai_only = "--ai-only" in args
    force = "--force" in args
    debug = "--debug" in args

    logging.basicConfig(level=logging.INFO, format=_CONSOLE_FMT, datefmt=_LOG_DATE)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    base = Path(__file__).resolve().parent.parent / "example_data" / "bird" / "dev_databases"
    if not base.exists():
        print(f"Error: {base} does not exist")
        sys.exit(1)

    db_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    if db_filter:
        db_dirs = [d for d in db_dirs if d.name == db_filter]
        if not db_dirs:
            print(f"Error: database '{db_filter}' not found")
            sys.exit(1)

    mode = "static only" if no_ai else ("AI only" if ai_only else "static + AI")
    print(f"=== BIRD Extract ({mode}) ===")
    print(f"Databases: {len(db_dirs)}\n")

    success, failed = [], []
    total_static = total_ai_col = total_agent = 0.0

    for i, db_dir in enumerate(db_dirs, 1):
        name = db_dir.name
        print(f"[{i}/{len(db_dirs)}] {name}")

        try:
            r = extract_one(str(db_dir), force=force, no_ai=no_ai,
                            ai_only=ai_only, debug=debug)
            total_static += r["static"]
            total_ai_col += r["ai_columns"]
            total_agent += r["agent"]

            parts = []
            if r["static"]: parts.append(f"Static: {r['static']:.1f}s")
            if r["ai_columns"]: parts.append(f"AI Cols: {r['ai_columns']:.1f}s")
            if r["agent"]: parts.append(f"Agent: {r['agent']:.1f}s")
            print(f"  {', '.join(parts)}")

            success.append(name)

        except Exception as e:
            logger.error(f"Failed: {name}: {e}")
            failed.append(name)
            print(f"  FAILED: {e}")

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
