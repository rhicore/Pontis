#!/usr/bin/env python3
"""BIRD 数据库提取脚本。"""
import logging
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.BIRD.common import (
    PONTIS_WORKSPACE_ROOT,
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
    "agent_schema_prepare",
    "agent_entity_hints",
    "agent_readme",
]

EMBEDDING_PIPELINE = [
    "semantic_embedding",
]

_CONSOLE_FMT = "%(asctime)s %(levelname)-5s | %(message)s"
_LOG_DATE = "%H:%M:%S"


def _sum_timings(timings: dict, names: list[str]) -> float:
    return sum(timings.get(name, 0.0) for name in names)


def extract_one(
    db_dir: str,
    preprocess_dir: str | Path | None = None,
    force: bool = False,
    no_ai: bool = False,
    ai_only: bool = False,
    agent_only: bool = False,
    debug: bool = False,
    train: bool = False,
    column_workers: int | None = None,
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
    if force:
        workspace.clear_graph()
        logger.info(f"  已清空 Neo4j 图谱: {name}")
    pontis_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "name": name,
        "static": 0.0,
        "ai_columns": 0.0,
        "ai_tables": 0.0,
        "ai_db": 0.0,
        "agent": 0.0,
        "schema_prepare": 0.0,
        "join_detect": 0.0,
        "disambiguate": 0.0,
        "entity_hints": 0.0,
        "readme": 0.0,
        "query_overview": 0.0,
        "embedding": 0.0,
        "preprocess_llm_calls": 0,
        "preprocess_llm_input_tokens": 0,
        "preprocess_llm_output_tokens": 0,
        "preprocess_llm_total_tokens": 0,
        "preprocess_embedding_calls": 0,
        "preprocess_embedding_input_tokens": 0,
        "preprocess_embedding_total_tokens": 0,
        "preprocess_total_tokens": 0,
    }

    with file_log_handler(str(pontis_dir / "extract.log")):
        logger.info(f"=== {name} ===")
        if not ai_only:
            static_timings = run_modules(
                STATIC_PIPELINE,
                workspace,
                config=config,
                options=RunOptions(
                    continue_on_error=True,
                    collect_timing=True,
                    module_kwargs={
                        "db_column_stats_approx": {"max_workers": column_workers}
                    } if column_workers else None,
                ),
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
            agent_timings = run_modules(
                agent_pipeline,
                workspace,
                config=config,
                options=RunOptions(
                    continue_on_error=True,
                    collect_timing=True,
                ),
            )
            result["schema_prepare"] = agent_timings.get("agent_schema_prepare", 0.0)
            result["agent"] = result["schema_prepare"]
            result["join_detect"] = agent_timings.get("agent_join_detect", 0.0)
            result["disambiguate"] = agent_timings.get("agent_disambiguate", 0.0)
            result["entity_hints"] = agent_timings.get("agent_entity_hints", 0.0)
            result["readme"] = agent_timings.get("agent_readme", 0.0)

            if result["ai_columns"]:
                logger.info(f"AI columns phase done: {result['ai_columns']:.1f}s")
            if result["ai_tables"]:
                logger.info(f"AI tables phase done: {result['ai_tables']:.1f}s")
            if result["ai_db"]:
                logger.info(f"AI db phase done: {result['ai_db']:.1f}s")
            if result["agent"]:
                logger.info(f"Schema prepare phase done: {result['agent']:.1f}s")
            if result["join_detect"]:
                logger.info(f"Join detect phase done: {result['join_detect']:.1f}s")
            if result["disambiguate"]:
                logger.info(f"Disambiguate phase done: {result['disambiguate']:.1f}s")
            if result["entity_hints"]:
                logger.info(f"Entity hints phase done: {result['entity_hints']:.1f}s")
            if result["readme"]:
                logger.info(f"README phase done: {result['readme']:.1f}s")

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

        if hasattr(config, "get_preprocess_token_metrics"):
            result.update(config.get_preprocess_token_metrics())
            logger.info(
                "Preprocess tokens: LLM=%s (in=%s, out=%s), embedding=%s, total=%s",
                result["preprocess_llm_total_tokens"],
                result["preprocess_llm_input_tokens"],
                result["preprocess_llm_output_tokens"],
                result["preprocess_embedding_total_tokens"],
                result["preprocess_total_tokens"],
            )

        logger.info(f"=== {name} done ===")

    return result


def _parse_run_id(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--run-id" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--run-id="):
            return arg.split("=", 1)[1]
    return None


def _parse_workers(argv: list[str]) -> int:
    for i, arg in enumerate(argv):
        if arg == "--workers" and i + 1 < len(argv):
            return max(1, int(argv[i + 1]))
        if arg.startswith("--workers="):
            return max(1, int(arg.split("=", 1)[1]))
    return 1


def _parse_column_workers(argv: list[str]) -> int | None:
    for i, arg in enumerate(argv):
        if arg == "--column-workers" and i + 1 < len(argv):
            return max(1, int(argv[i + 1]))
        if arg.startswith("--column-workers="):
            return max(1, int(arg.split("=", 1)[1]))
    return None


def _parse_modules(argv: list[str]) -> list[str] | None:
    for i, arg in enumerate(argv):
        if arg == "--modules" and i + 1 < len(argv):
            return [name.strip() for name in argv[i + 1].split(",") if name.strip()]
        if arg.startswith("--modules="):
            return [name.strip() for name in arg.split("=", 1)[1].split(",") if name.strip()]
    return None


def _parse_db_filter(argv: list[str]) -> str | None:
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg in {"--run-id", "--workers", "--column-workers", "--modules"}:
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
    workers = _parse_workers(argv)
    column_workers = _parse_column_workers(argv)
    selected_modules = _parse_modules(argv)
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

    if selected_modules:
        mode = f"selected modules: {','.join(selected_modules)}"
    elif no_ai:
        mode = "static only"
    elif agent_only:
        mode = "static + agent only" if not ai_only else "agent only"
    else:
        mode = "AI only" if ai_only else "static + AI"
    split = "train" if train else "dev"
    print(f"=== BIRD Extract ({split}, {mode}) ===")
    print(f"Databases: {len(db_dirs)}\n")
    print(f"Workers: {workers}")
    print(f"Column workers/db: {column_workers or 'default'}")
    print(f"Run id: {get_run_id()}")
    print(f"Preprocess logs: {PONTIS_WORKSPACE_ROOT / 'preprocess_logs' / get_run_name(train)}\n")

    registry = get_registry()
    if selected_modules:
        unknown_modules = [name for name in selected_modules if name not in registry]
        if unknown_modules:
            print(f"Error: unknown module(s): {', '.join(unknown_modules)}")
            sys.exit(1)

    success, failed = [], []
    all_results = []
    total_static = total_ai_col = total_ai_tbl = total_ai_db = 0.0
    total_agent = total_join = total_disambig = total_entity_hints = total_readme = 0.0
    total_preprocess_llm_tokens = 0
    total_preprocess_embedding_tokens = 0
    total_preprocess_tokens = 0

    def run_db(db_dir: Path) -> dict:
        name = db_dir.name
        if selected_modules:
            from extractor.engine import RunOptions, init_workspace, run_modules

            workspace, config = init_workspace(str(db_dir), verbose=debug)
            pontis_dir = get_preprocess_dir(name, train)
            if force:
                workspace.clear_graph()
                logger.info(f"  已清空 Neo4j 图谱: {name}")
            pontis_dir.mkdir(parents=True, exist_ok=True)
            result = {
                "name": name,
                "static": 0.0,
                "ai_columns": 0.0,
                "ai_tables": 0.0,
                "ai_db": 0.0,
                "agent": 0.0,
                "schema_prepare": 0.0,
                "join_detect": 0.0,
                "disambiguate": 0.0,
                "entity_hints": 0.0,
                "readme": 0.0,
                "query_overview": 0.0,
                "embedding": 0.0,
                "preprocess_llm_calls": 0,
                "preprocess_llm_input_tokens": 0,
                "preprocess_llm_output_tokens": 0,
                "preprocess_llm_total_tokens": 0,
                "preprocess_embedding_calls": 0,
                "preprocess_embedding_input_tokens": 0,
                "preprocess_embedding_total_tokens": 0,
                "preprocess_total_tokens": 0,
            }
            with file_log_handler(str(pontis_dir / "extract.log")):
                logger.info(f"=== {name} selected modules ===")
                timings = run_modules(
                    selected_modules,
                    workspace,
                    config=config,
                    options=RunOptions(continue_on_error=True, collect_timing=True),
                )
                result["schema_prepare"] = timings.get("agent_schema_prepare", 0.0)
                result["agent"] = result["schema_prepare"]
                result["join_detect"] = timings.get("agent_join_detect", 0.0)
                result["disambiguate"] = timings.get("agent_disambiguate", 0.0)
                result["entity_hints"] = timings.get("agent_entity_hints", 0.0)
                result["readme"] = timings.get("agent_readme", 0.0)
                result["embedding"] = timings.get("semantic_embedding", 0.0)
                if hasattr(config, "get_preprocess_token_metrics"):
                    result.update(config.get_preprocess_token_metrics())
                logger.info(f"=== {name} selected modules done ===")
            return result
        return extract_one(
            str(db_dir),
            preprocess_dir=get_preprocess_dir(name, train),
            force=force,
            no_ai=no_ai,
            ai_only=ai_only,
            agent_only=agent_only,
            debug=debug,
            train=train,
            column_workers=column_workers,
        )

    def record_result(result: dict) -> None:
        nonlocal total_static, total_ai_col, total_ai_tbl, total_ai_db
        nonlocal total_agent, total_join, total_disambig, total_entity_hints, total_readme
        nonlocal total_preprocess_llm_tokens, total_preprocess_embedding_tokens
        nonlocal total_preprocess_tokens
        total_static += result["static"]
        total_ai_col += result["ai_columns"]
        total_ai_tbl += result["ai_tables"]
        total_ai_db += result["ai_db"]
        total_agent += result["agent"]
        total_join += result["join_detect"]
        total_disambig += result["disambiguate"]
        total_entity_hints += result.get("entity_hints", 0.0)
        total_readme += result["readme"]
        total_preprocess_llm_tokens += int(result.get("preprocess_llm_total_tokens", 0) or 0)
        total_preprocess_embedding_tokens += int(result.get("preprocess_embedding_total_tokens", 0) or 0)
        total_preprocess_tokens += int(result.get("preprocess_total_tokens", 0) or 0)
        all_results.append(result)

    def format_parts(result: dict) -> str:
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
            parts.append(f"Schema: {result['agent']:.1f}s")
        if result["join_detect"]:
            parts.append(f"Join: {result['join_detect']:.1f}s")
        if result["disambiguate"]:
            parts.append(f"Disambig: {result['disambiguate']:.1f}s")
        if result.get("entity_hints"):
            parts.append(f"Hints: {result['entity_hints']:.1f}s")
        if result["readme"]:
            parts.append(f"README: {result['readme']:.1f}s")
        if result["embedding"]:
            parts.append(f"Embedding: {result['embedding']:.1f}s")
        if result.get("preprocess_total_tokens"):
            parts.append(
                "Preprocess tokens: "
                f"LLM {result.get('preprocess_llm_total_tokens', 0)}, "
                f"Emb {result.get('preprocess_embedding_total_tokens', 0)}, "
                f"Total {result.get('preprocess_total_tokens', 0)}"
            )
        return ", ".join(parts)

    if workers == 1 or len(db_dirs) <= 1:
        for i, db_dir in enumerate(db_dirs, 1):
            name = db_dir.name
            print(f"[{i}/{len(db_dirs)}] {name}")
            try:
                result = run_db(db_dir)
                record_result(result)
                print(f"  {format_parts(result)}")
                success.append(name)
            except Exception as e:
                logger.error(f"Failed: {name}: {e}")
                failed.append(name)
                print(f"  FAILED: {e}")
            print()
    else:
        print(f"Submitting {len(db_dirs)} extract tasks with {workers} workers\n")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(run_db, db_dir): (i, db_dir.name)
                for i, db_dir in enumerate(db_dirs, 1)
            }
            for future in as_completed(futures):
                i, name = futures[future]
                print(f"[{i}/{len(db_dirs)}] {name}")
                try:
                    result = future.result()
                    record_result(result)
                    print(f"  {format_parts(result)}")
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
        total_agent + total_join + total_disambig + total_entity_hints + total_readme
    )
    print(
        f"Time: static {total_static:.1f}s, AI cols {total_ai_col:.1f}s, "
        f"AI tables {total_ai_tbl:.1f}s, AI db {total_ai_db:.1f}s, "
        f"schema {total_agent:.1f}s, join {total_join:.1f}s, "
        f"disambig {total_disambig:.1f}s, hints {total_entity_hints:.1f}s, "
        f"readme {total_readme:.1f}s, "
        f"total {total_all:.1f}s"
    )
    print(
        "Preprocess tokens: "
        f"LLM {total_preprocess_llm_tokens}, "
        f"embedding {total_preprocess_embedding_tokens}, "
        f"total {total_preprocess_tokens}"
    )
    summary = {
        "run_id": get_run_id(),
        "split": split,
        "databases": len(db_dirs),
        "success": success,
        "failed": failed,
        "time_seconds": {
            "static": total_static,
            "ai_columns": total_ai_col,
            "ai_tables": total_ai_tbl,
            "ai_db": total_ai_db,
            "schema": total_agent,
            "join": total_join,
            "disambiguate": total_disambig,
            "entity_hints": total_entity_hints,
            "readme": total_readme,
            "total": total_all,
        },
        "preprocess_tokens": {
            "llm_total_tokens": total_preprocess_llm_tokens,
            "embedding_total_tokens": total_preprocess_embedding_tokens,
            "total_tokens": total_preprocess_tokens,
        },
        "per_database": sorted(all_results, key=lambda row: row["name"]),
    }
    summary_path = PONTIS_WORKSPACE_ROOT / "preprocess_logs" / get_run_name(train) / "extract_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Summary: {summary_path}")
    if failed:
        print(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
