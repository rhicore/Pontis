#!/usr/bin/env python3
"""Prepare and preprocess Spider2-Snow filesystem projects for Pontis."""

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

from scripts.preprocess_engine import RunOptions, file_log_handler, get_registry, init_workspace, run_modules
from scripts.spider.common import (
    get_preprocess_dir,
    get_run_id,
    get_run_name,
    group_cases_by_db,
    ensure_spider2_snow_neo4j,
    load_spider2_snow_cases,
    parse_csv_arg,
    prepare_spider2_snow_project,
    set_run_id,
    sync_spider2_snow_pontis_config,
)


logger = logging.getLogger(__name__)

EXTRACT_PIPELINE = [
    "spider2_snow_schema",
    "db_table_group",
    "db_column_stats",
    "db_column_overlap",
]
TOPIC_PIPELINE = ["agent_topic_group"]
NAVIGATION_PIPELINE = ["agent_spider_navigation_prepare"]
LANDSCAPE_PIPELINE = ["schema_landscape"]
EMBEDDING_PIPELINE = ["semantic_embedding"]

SPIDER_OVERLAP_KWARGS = {
    # Use the widest useful non-zero Jaccard gate. With 256 permutations,
    # 0.0001 is effectively the requirement of at least one MinHash collision.
    "value_match_method": "snowflake_minhash",
    "filter_pipeline": [
        {"name": "same_schema", "threshold": 1},
        {"name": "different_table_group", "threshold": 1},
        {"name": "name_token_overlap", "threshold": 1},
        {"name": "domain_compatible", "threshold": 1},
        {"name": "shape_compatible", "threshold": 1},
        {"name": "value_overlap", "metric": "overlap_coefficient", "threshold": 0},
    ],
    "same_schema_only": True,
    "skip_same_table_group": True,
    "domain_filter_enabled": True,
    "shape_filter_enabled": True,
    "key_like_only": False,
    "require_name_token_overlap": True,
    "name_token_overlap_first": False,
    "minhash_num_perm": 256,
    "minhash_min_matching_hashes": 1,
    "minhash_jaccard_threshold": 0.0001,
    "max_value_candidate_pairs": 1_000_000,
    "snowflake_minhash_column_batch_size": 1,
    "snowflake_minhash_value_partitions": 2,
    "snowflake_minhash_max_warehouse_running": 2,
    "snowflake_minhash_warehouse_poll_seconds": 30,
    "column_domain_enabled": True,
    "pattern_table_domain_enabled": True,
    "pattern_table_domain_threshold": 0.8,
}

SPIDER_OVERLAP_DB_OVERRIDES = {
    "BRAZE_USER_EVENT_DEMO_DATASET": {
        "snowflake_minhash_value_partitions": 8,
    },
}

SPIDER_COLUMN_STATS_KWARGS = {
    "sample_size": 10,
    "topk_size": 5,
    "max_workers": 2,
    "cardinality_mode": "exact",
}


def extract_one(
    db_id: str,
    cases: list,
    *,
    force: bool = False,
    extract_only: bool = False,
    agent_only: bool = False,
    skip_topic: bool = False,
    skip_navigation: bool = False,
    skip_landscape: bool = False,
    skip_embedding: bool = False,
    debug: bool = False,
) -> dict:
    prepared = prepare_spider2_snow_project(db_id, cases, force=force and not agent_only)
    project_dir = Path(prepared["project_dir"])
    preprocess_dir = get_preprocess_dir(db_id)
    preprocess_dir.mkdir(parents=True, exist_ok=True)

    workspace, config = init_workspace(str(project_dir), verbose=debug)
    if force and not agent_only:
        workspace.clear_graph()

    registry = get_registry()
    overlap_kwargs = {
        **SPIDER_OVERLAP_KWARGS,
        **SPIDER_OVERLAP_DB_OVERRIDES.get(db_id, {}),
    }
    pipeline: list[str] = []
    if not agent_only:
        pipeline.extend(name for name in EXTRACT_PIPELINE if name in registry)
    if not extract_only:
        if not skip_topic:
            pipeline.extend(name for name in TOPIC_PIPELINE if name in registry)
        if not skip_navigation:
            pipeline.extend(name for name in NAVIGATION_PIPELINE if name in registry)
        if not skip_landscape:
            pipeline.extend(name for name in LANDSCAPE_PIPELINE if name in registry)
    if not extract_only and not skip_embedding:
        pipeline.extend(name for name in EMBEDDING_PIPELINE if name in registry)

    result = {
        **prepared,
        "run_id": get_run_id(),
        "run_name": get_run_name(),
        "modules": pipeline,
        "timings": {},
        "extract": 0.0,
        "topic": 0.0,
        "navigation": 0.0,
        "landscape": 0.0,
        "embedding": 0.0,
        "preprocess_llm_calls": 0,
        "preprocess_llm_total_tokens": 0,
        "preprocess_embedding_calls": 0,
        "preprocess_embedding_total_tokens": 0,
        "preprocess_total_tokens": 0,
    }

    with file_log_handler(str(preprocess_dir / "extract.log")):
        logger.info("=== Spider2-Snow preprocess: %s ===", db_id)
        logger.info("Project: %s", project_dir)
        timings = run_modules(
            pipeline,
            workspace,
            config=config,
            options=RunOptions(
                continue_on_error=False,
                collect_timing=True,
                module_kwargs={
                    "db_column_stats": SPIDER_COLUMN_STATS_KWARGS,
                    "db_column_overlap": overlap_kwargs,
                },
            ),
        )
        result["timings"] = {name: round(value, 3) for name, value in timings.items()}
        result["extract"] = round(sum(timings.get(name, 0.0) for name in EXTRACT_PIPELINE), 3)
        result["topic"] = round(sum(timings.get(name, 0.0) for name in TOPIC_PIPELINE), 3)
        result["navigation"] = round(sum(timings.get(name, 0.0) for name in NAVIGATION_PIPELINE), 3)
        result["landscape"] = round(sum(timings.get(name, 0.0) for name in LANDSCAPE_PIPELINE), 3)
        result["embedding"] = round(sum(timings.get(name, 0.0) for name in EMBEDDING_PIPELINE), 3)
        if hasattr(config, "get_preprocess_token_metrics"):
            result.update(config.get_preprocess_token_metrics())
        logger.info("=== Spider2-Snow preprocess done: %s ===", db_id)

    (preprocess_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Comma-separated db_id filter.")
    parser.add_argument("--instances", help="Comma-separated Spider2-Snow instance_id filter.")
    parser.add_argument(
        "--dev-only",
        "--gold-sql-only",
        action="store_true",
        dest="dev_only",
        help="Only select Spider2-Snow cases that have local gold SQL files for correctness debugging.",
    )
    parser.add_argument("--limit", type=int, help="Limit selected cases before grouping by db.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--extract-only", action="store_true", help="Run only official schema/table-group extraction.")
    parser.add_argument("--agent-only", action="store_true", help="Skip extraction and rerun explorer/embedding on the existing graph.")
    parser.add_argument("--skip-topic", action="store_true")
    parser.add_argument("--skip-navigation", action="store_true")
    parser.add_argument("--skip-landscape", action="store_true")
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
    graph_config_path = ensure_spider2_snow_neo4j()

    cases = load_spider2_snow_cases(
        db=args.db,
        instances=parse_csv_arg(args.instances),
        limit=args.limit,
        dev_only=args.dev_only,
    )
    if not cases:
        raise SystemExit("No Spider2-Snow cases selected.")
    grouped = group_cases_by_db(cases)

    print(f"Spider2-Snow preprocess run: {get_run_name()} ({get_run_id()})")
    if args.dev_only:
        print("Split: dev-only / gold-SQL subset")
    print(f"Databases: {len(grouped)}, cases: {len(cases)}")
    print(f"Pontis config: {config_path}")
    print(f"Spider2-Snow Neo4j: {graph_config_path}")

    results = []
    if args.workers <= 1:
        for db_id, db_cases in grouped.items():
            results.append(
                extract_one(
                    db_id,
                    db_cases,
                    force=args.force,
                    extract_only=args.extract_only,
                    agent_only=args.agent_only,
                    skip_topic=args.skip_topic,
                    skip_navigation=args.skip_navigation,
                    skip_landscape=args.skip_landscape,
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
                    extract_only=args.extract_only,
                    agent_only=args.agent_only,
                    skip_topic=args.skip_topic,
                    skip_navigation=args.skip_navigation,
                    skip_landscape=args.skip_landscape,
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
