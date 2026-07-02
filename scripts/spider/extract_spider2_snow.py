#!/usr/bin/env python3
"""Prepare and extract Spider2-Snow filesystem projects for Pontis."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from extractor.engine import RunOptions, file_log_handler, get_registry, init_workspace, run_modules
from scripts.spider.common import (
    get_preprocess_dir,
    get_run_id,
    get_run_name,
    group_cases_by_db,
    load_spider2_snow_cases,
    parse_csv_arg,
    prepare_spider2_snow_project,
    set_run_id,
    sync_spider2_snow_pontis_config,
)


logger = logging.getLogger(__name__)

FS_PIPELINE = [
    "json_pattern",
    "csv_column_stats",
    "csv_column_sample",
    "csv_column_topk",
]
EMBEDDING_PIPELINE = ["semantic_embedding"]


def extract_one(
    db_id: str,
    cases: list,
    *,
    force: bool = False,
    skip_embedding: bool = False,
    debug: bool = False,
) -> dict:
    prepared = prepare_spider2_snow_project(db_id, cases, force=force)
    project_dir = Path(prepared["project_dir"])
    preprocess_dir = get_preprocess_dir(db_id)
    preprocess_dir.mkdir(parents=True, exist_ok=True)

    workspace, config = init_workspace(str(project_dir), verbose=debug)
    if force:
        workspace.clear_graph()
    if prepared.get("graph_uri"):
        workspace.refresh_sources(modules=["snowflake"])

    registry = get_registry()
    pipeline = [name for name in FS_PIPELINE if name in registry]
    if not skip_embedding:
        pipeline.extend(name for name in EMBEDDING_PIPELINE if name in registry)

    result = {
        **prepared,
        "run_id": get_run_id(),
        "run_name": get_run_name(),
        "modules": pipeline,
        "timings": {},
        "preprocess_llm_calls": 0,
        "preprocess_llm_total_tokens": 0,
        "preprocess_embedding_calls": 0,
        "preprocess_embedding_total_tokens": 0,
        "preprocess_total_tokens": 0,
    }

    with file_log_handler(str(preprocess_dir / "extract.log")):
        logger.info("=== Spider2-Snow extract: %s ===", db_id)
        logger.info("Project: %s", project_dir)
        timings = run_modules(
            pipeline,
            workspace,
            config=config,
            options=RunOptions(continue_on_error=False, collect_timing=True),
        )
        result["timings"] = {name: round(value, 3) for name, value in timings.items()}
        if hasattr(config, "get_preprocess_token_metrics"):
            result.update(config.get_preprocess_token_metrics())
        logger.info("=== Spider2-Snow extract done: %s ===", db_id)

    (preprocess_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Comma-separated db_id filter.")
    parser.add_argument("--instances", help="Comma-separated Spider2-Snow instance_id filter.")
    parser.add_argument("--limit", type=int, help="Limit selected cases before grouping by db.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-embedding", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.run_id:
        set_run_id(args.run_id)

    config_path = sync_spider2_snow_pontis_config()

    cases = load_spider2_snow_cases(
        db=args.db,
        instances=parse_csv_arg(args.instances),
        limit=args.limit,
    )
    if not cases:
        raise SystemExit("No Spider2-Snow cases selected.")
    grouped = group_cases_by_db(cases)

    print(f"Spider2-Snow extract run: {get_run_name()} ({get_run_id()})")
    print(f"Databases: {len(grouped)}, cases: {len(cases)}")
    print(f"Pontis config: {config_path}")

    results = []
    if args.workers <= 1:
        for db_id, db_cases in grouped.items():
            results.append(
                extract_one(
                    db_id,
                    db_cases,
                    force=args.force,
                    skip_embedding=args.skip_embedding,
                    debug=args.debug,
                )
            )
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    extract_one,
                    db_id,
                    db_cases,
                    force=args.force,
                    skip_embedding=args.skip_embedding,
                    debug=args.debug,
                ): db_id
                for db_id, db_cases in grouped.items()
            }
            for future in as_completed(futures):
                db_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.exception("Extract failed for %s", db_id)
                    result = {"db_id": db_id, "error": f"{type(exc).__name__}: {exc}"}
                results.append(result)

    summary = {
        "run_id": get_run_id(),
        "run_name": get_run_name(),
        "databases": len(grouped),
        "cases": len(cases),
        "results": sorted(results, key=lambda row: row.get("db_id", "")),
    }
    summary_dir = Path(results[0]["project_dir"]).parents[1] if results and "project_dir" in results[0] else None
    output_dir = summary_dir or Path.cwd()
    output_path = output_dir / f"extract_summary_{get_run_name()}.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Summary: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
