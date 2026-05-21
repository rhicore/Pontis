#!/usr/bin/env python3
"""BIRD 数据库提取脚本。"""
import json
import logging
import shutil
import sys
from pathlib import Path

from scripts.BIRD.common import (
    PONTIS_WORKSPACE_ROOT,
    get_data_dir,
    get_db_base,
    get_preprocess_dir,
    get_run_id,
    get_run_name,
    set_run_id,
)
from extractor.engine import (
    RunOptions,
    file_log_handler,
    get_registry,
    init_workspace,
    run_modules,
)

logger = logging.getLogger(__name__)

STATIC_PIPELINE = [
    "db_column_stats_approx",
    "db_fk_validate",
    "db_column_overlap",
]

AI_PIPELINE = [
    "ai_db_column_summary",
]

AGENT_PIPELINE = [
    "agent_analyze",
    "agent_join_detect",
    "agent_disambiguate",
    "agent_readme",
    "agent_query_overview",
]

EMBEDDING_PIPELINE = [
    "semantic_embedding",
]

_CONSOLE_FMT = "%(asctime)s %(levelname)-5s | %(message)s"
_LOG_DATE = "%H:%M:%S"


def _sum_timings(timings: dict, names: list[str]) -> float:
    return sum(timings.get(name, 0.0) for name in names)


def _load_query_cases(db_id: str, train: bool) -> list[dict]:
    """Load question/evidence cases for one BIRD database."""
    path = get_data_dir(train) / ("train.json" if train else "dev.json")
    if not path.exists():
        logger.warning("BIRD case file not found: %s", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        logger.warning("BIRD case file is not a JSON list: %s", path)
        return []
    cases = []
    for item in data:
        if not isinstance(item, dict) or str(item.get("db_id", "")) != db_id:
            continue
        cases.append({
            "question_id": item.get("question_id", item.get("id")),
            "db_id": item.get("db_id"),
            "question": item.get("question", ""),
            "evidence": item.get("evidence", ""),
        })
    return cases


def extract_one(
    db_dir: str,
    preprocess_dir: str | Path | None = None,
    force: bool = False,
    no_ai: bool = False,
    ai_only: bool = False,
    agent_only: bool = False,
    debug: bool = False,
    train: bool = False,
) -> dict:
    """提取单个 BIRD 数据库目录。"""
    db_dir = Path(db_dir).resolve()
    name = db_dir.name
    pontis_dir = Path(preprocess_dir).resolve() if preprocess_dir else get_preprocess_dir(name, train=train)

    if force and pontis_dir.exists():
        try:
            shutil.rmtree(pontis_dir)
        except OSError as e:
            raise RuntimeError(
                f"failed to remove existing preprocess output for '{name}'. "
                f"This usually means another extract process is still using the directory: {e}"
            ) from e
        logger.info(f"  已删除旧 preprocess 输出: {name}")

    workspace, config = init_workspace(str(db_dir), verbose=debug)
    pontis_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "name": name,
        "static": 0.0,
        "ai_columns": 0.0,
        "ai_tables": 0.0,
        "ai_db": 0.0,
        "agent": 0.0,
        "join_detect": 0.0,
        "disambiguate": 0.0,
        "readme": 0.0,
        "query_overview": 0.0,
        "embedding": 0.0,
    }

    with file_log_handler(str(pontis_dir / "extract.log")):
        logger.info(f"=== {name} ===")
        if not ai_only:
            static_timings = run_modules(
                STATIC_PIPELINE,
                workspace,
                config=config,
                options=RunOptions(continue_on_error=True, collect_timing=True),
            )
            result["static"] = _sum_timings(static_timings, STATIC_PIPELINE)
            logger.info(f"Static phase done: {result['static']:.1f}s")

        if not no_ai:
            registry = get_registry()

            if not agent_only:
                ai_pipeline = [name for name in AI_PIPELINE if name in registry]
                ai_timings = run_modules(
                    ai_pipeline,
                    workspace,
                    config=config,
                    options=RunOptions(continue_on_error=True, collect_timing=True),
                )
                result["ai_columns"] = ai_timings.get("ai_db_column_summary", 0.0)
                result["ai_tables"] = ai_timings.get("ai_db_table_summary", 0.0)
                result["ai_db"] = ai_timings.get("ai_db_summary", 0.0)

            agent_pipeline = [name for name in AGENT_PIPELINE if name in registry]
            module_kwargs = {}
            if "agent_query_overview" in agent_pipeline:
                module_kwargs["agent_query_overview"] = {
                    "cases": _load_query_cases(name, train=train),
                }
            agent_timings = run_modules(
                agent_pipeline,
                workspace,
                config=config,
                options=RunOptions(
                    continue_on_error=True,
                    collect_timing=True,
                    module_kwargs=module_kwargs,
                ),
            )
            result["agent"] = agent_timings.get("agent_analyze", 0.0)
            result["join_detect"] = agent_timings.get("agent_join_detect", 0.0)
            result["disambiguate"] = agent_timings.get("agent_disambiguate", 0.0)
            result["readme"] = agent_timings.get("agent_readme", 0.0)
            result["query_overview"] = agent_timings.get("agent_query_overview", 0.0)

            if result["ai_columns"]:
                logger.info(f"AI columns phase done: {result['ai_columns']:.1f}s")
            if result["ai_tables"]:
                logger.info(f"AI tables phase done: {result['ai_tables']:.1f}s")
            if result["ai_db"]:
                logger.info(f"AI db phase done: {result['ai_db']:.1f}s")
            if result["agent"]:
                logger.info(f"Agent phase done: {result['agent']:.1f}s")
            if result["join_detect"]:
                logger.info(f"Join detect phase done: {result['join_detect']:.1f}s")
            if result["disambiguate"]:
                logger.info(f"Disambiguate phase done: {result['disambiguate']:.1f}s")
            if result["readme"]:
                logger.info(f"README phase done: {result['readme']:.1f}s")
            if result["query_overview"]:
                logger.info(f"Query overview phase done: {result['query_overview']:.1f}s")

        registry = get_registry()
        embedding_pipeline = [name for name in EMBEDDING_PIPELINE if name in registry]
        embedding_timings = run_modules(
            embedding_pipeline,
            workspace,
            config=config,
            options=RunOptions(continue_on_error=True, collect_timing=True),
        )
        result["embedding"] = embedding_timings.get("semantic_embedding", 0.0)
        if result["embedding"]:
            logger.info(f"Embedding phase done: {result['embedding']:.1f}s")

        logger.info(f"=== {name} done ===")

    return result


def _parse_run_id(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--run-id" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--run-id="):
            return arg.split("=", 1)[1]
    return None


def _parse_db_filter(argv: list[str]) -> str | None:
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg == "--run-id":
            skip_next = True
            continue
        if arg.startswith("--"):
            continue
        return arg
    return None


def main() -> None:
    argv = sys.argv[1:]
    run_id = _parse_run_id(argv)
    if run_id:
        set_run_id(run_id)

    args = set(argv)
    db_filter = _parse_db_filter(argv)

    no_ai = "--no-ai" in args or "--static-only" in args
    ai_only = "--ai-only" in args
    agent_only = "--agent-only" in args
    force = "--force" in args
    debug = "--debug" in args
    train = "--train" in args

    logging.basicConfig(level=logging.INFO, format=_CONSOLE_FMT, datefmt=_LOG_DATE)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    base = get_db_base(train)
    if not base.exists():
        print(f"Error: {base} does not exist")
        sys.exit(1)

    db_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    if db_filter:
        db_dirs = [d for d in db_dirs if d.name == db_filter]
        if not db_dirs:
            print(f"Error: database '{db_filter}' not found")
            sys.exit(1)

    if no_ai:
        mode = "static only"
    elif agent_only:
        mode = "static + agent only" if not ai_only else "agent only"
    else:
        mode = "AI only" if ai_only else "static + AI"
    split = "train" if train else "dev"
    print(f"=== BIRD Extract ({split}, {mode}) ===")
    print(f"Databases: {len(db_dirs)}\n")
    print(f"Run id: {get_run_id()}")
    print(f"Preprocess logs: {PONTIS_WORKSPACE_ROOT / 'preprocess_logs' / get_run_name(train)}\n")

    success, failed = [], []
    total_static = total_ai_col = total_ai_tbl = total_ai_db = 0.0
    total_agent = total_join = total_disambig = total_readme = 0.0
    total_query_overview = 0.0

    for i, db_dir in enumerate(db_dirs, 1):
        name = db_dir.name
        print(f"[{i}/{len(db_dirs)}] {name}")

        try:
            result = extract_one(
                str(db_dir),
                preprocess_dir=get_preprocess_dir(name, train),
                force=force,
                no_ai=no_ai,
                ai_only=ai_only,
                agent_only=agent_only,
                debug=debug,
                train=train,
            )
            total_static += result["static"]
            total_ai_col += result["ai_columns"]
            total_ai_tbl += result["ai_tables"]
            total_ai_db += result["ai_db"]
            total_agent += result["agent"]
            total_join += result["join_detect"]
            total_disambig += result["disambiguate"]
            total_readme += result["readme"]
            total_query_overview += result["query_overview"]

            parts = []
            if result["static"]:
                parts.append(f"Static: {result['static']:.1f}s")
            if result["ai_columns"]:
                parts.append(f"AI Cols: {result['ai_columns']:.1f}s")
            if result["ai_tables"]:
                parts.append(f"AI Tables: {result['ai_tables']:.1f}s")
            if result["ai_db"]:
                parts.append(f"AI DB: {result['ai_db']:.1f}s")
            if result["agent"]:
                parts.append(f"Agent: {result['agent']:.1f}s")
            if result["join_detect"]:
                parts.append(f"Join: {result['join_detect']:.1f}s")
            if result["disambiguate"]:
                parts.append(f"Disambig: {result['disambiguate']:.1f}s")
            if result["readme"]:
                parts.append(f"README: {result['readme']:.1f}s")
            if result["query_overview"]:
                parts.append(f"Query Overview: {result['query_overview']:.1f}s")
            print(f"  {', '.join(parts)}")
            success.append(name)
        except Exception as e:
            logger.error(f"Failed: {name}: {e}")
            failed.append(name)
            print(f"  FAILED: {e}")

        print()

    print("=" * 40)
    print(f"Done: {len(success)} ok, {len(failed)} failed")
    total_all = (
        total_static + total_ai_col + total_ai_tbl + total_ai_db +
        total_agent + total_join + total_disambig + total_readme +
        total_query_overview
    )
    print(
        f"Time: static {total_static:.1f}s, AI cols {total_ai_col:.1f}s, "
        f"AI tables {total_ai_tbl:.1f}s, AI db {total_ai_db:.1f}s, "
        f"agent {total_agent:.1f}s, join {total_join:.1f}s, "
        f"disambig {total_disambig:.1f}s, readme {total_readme:.1f}s, "
        f"query overview {total_query_overview:.1f}s, total {total_all:.1f}s"
    )
    if failed:
        print(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
