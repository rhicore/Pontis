#!/usr/bin/env python3
"""BIRD 数据库提取脚本。"""
import argparse
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
from scripts.preprocess_engine import (
    RunOptions,
    file_log_handler,
    get_registry,
    init_workspace,
    run_modules,
)

logger = logging.getLogger(__name__)

STATIC_PIPELINE = [
    "db_column_stats",
    "db_fk_validate",
    "db_column_domain",
]

# BIRD deliberately keeps the simple legacy policy: compare all physical
# SQLite columns exactly and add independent name evidence. Spider's logical
# columns, bounded samples, and online clustering are unreachable here.
BIRD_COLUMN_DOMAIN_OPTIONS = {
    "value_match_method": "sql",
    "filter_pipeline": [
        {
            "name": "value_overlap",
            "metric": "overlap_coefficient",
            "threshold": 0,
        },
    ],
    # This is the only non-BIRD default that would filter the legacy candidate
    # set before exact value comparison.
    "domain_filter_enabled": False,
    "max_value_candidate_pairs": 1_000_000,
}

OFFICIAL_DESCRIPTION_PIPELINE = [
    "bird_official_description_extract",
]

AGENT_PIPELINE = [
    "agent_schema_prepare",
    "agent_column_domain_review",
    "agent_disambiguate",
    "agent_bird_profile",
    "agent_description_audit",
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
        "agent": 0.0,
        "schema_prepare": 0.0,
        "relation_review": 0.0,
        "business_profile": 0.0,
        "description_review": 0.0,
        "description_audit": 0.0,
        "disambiguate": 0.0,
        "readme": 0.0,
        "embedding": 0.0,
        "preprocess_llm_calls": 0,
        "preprocess_llm_input_tokens": 0,
        "preprocess_llm_cached_input_tokens": 0,
        "preprocess_llm_uncached_input_tokens": 0,
        "preprocess_llm_output_tokens": 0,
        "preprocess_llm_total_tokens": 0,
        "preprocess_embedding_calls": 0,
        "preprocess_embedding_input_tokens": 0,
        "preprocess_embedding_total_tokens": 0,
        "preprocess_total_tokens": 0,
    }

    with file_log_handler(str(pontis_dir / "extract.log")):
        logger.info(f"=== {name} ===")
        run_static = not ai_only and not agent_only
        run_agents = not no_ai
        run_embedding = not no_ai and not agent_only

        if run_static:
            static_timings = run_modules(
                STATIC_PIPELINE,
                workspace,
                config=config,
                options=RunOptions(
                    continue_on_error=False,
                    collect_timing=True,
                    module_kwargs={
                        "db_column_domain": {
                            "strategy": "pairwise_filter",
                            "strategy_options": BIRD_COLUMN_DOMAIN_OPTIONS,
                        },
                    },
                ),
            )
            result["static"] = _sum_timings(static_timings, STATIC_PIPELINE)
            logger.info(f"Static phase done: {result['static']:.1f}s")

            registry = get_registry()
            official_pipeline = [name for name in OFFICIAL_DESCRIPTION_PIPELINE if name in registry]
            official_timings = run_modules(
                official_pipeline,
                workspace,
                config=config,
                options=RunOptions(continue_on_error=False, collect_timing=True),
            )
            result["description_review"] = official_timings.get("bird_official_description_extract", 0.0)
            if result["description_review"]:
                logger.info(f"Official description phase done: {result['description_review']:.1f}s")

        if run_agents:
            registry = get_registry()

            agent_pipeline = [name for name in AGENT_PIPELINE if name in registry]
            agent_timings = run_modules(
                agent_pipeline,
                workspace,
                config=config,
                options=RunOptions(
                    continue_on_error=False,
                    collect_timing=True,
                ),
            )
            result["schema_prepare"] = agent_timings.get("agent_schema_prepare", 0.0)
            result["agent"] = result["schema_prepare"]
            result["relation_review"] = agent_timings.get("agent_column_domain_review", 0.0)
            result["business_profile"] = agent_timings.get("agent_bird_profile", 0.0)
            result["description_audit"] = agent_timings.get("agent_description_audit", 0.0)
            result["disambiguate"] = agent_timings.get("agent_disambiguate", 0.0)
            result["readme"] = agent_timings.get("agent_readme", 0.0)

            if result["agent"]:
                logger.info(f"Schema prepare phase done: {result['agent']:.1f}s")
            if result["relation_review"]:
                logger.info(f"Relation/disambiguation review phase done: {result['relation_review']:.1f}s")
            if result["business_profile"]:
                logger.info(f"Business profile phase done: {result['business_profile']:.1f}s")
            if result["description_audit"]:
                logger.info(f"Description audit phase done: {result['description_audit']:.1f}s")
            if result["disambiguate"]:
                logger.info(f"Disambiguate phase done: {result['disambiguate']:.1f}s")
            if result["readme"]:
                logger.info(f"README phase done: {result['readme']:.1f}s")

        if run_embedding:
            registry = get_registry()
            embedding_pipeline = [name for name in EMBEDDING_PIPELINE if name in registry]
            embedding_timings = run_modules(
                embedding_pipeline,
                workspace,
                config=config,
                options=RunOptions(continue_on_error=False, collect_timing=True),
            )
            result["embedding"] = embedding_timings.get("semantic_embedding", 0.0)
            if result["embedding"]:
                logger.info(f"Embedding phase done: {result['embedding']:.1f}s")

        if run_static and run_agents and run_embedding:
            from scripts.BIRD.readiness import assert_bird_graph_ready

            assert_bird_graph_ready(workspace, config=config)
            logger.info("BIRD graph readiness check passed")

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract BIRD databases into Pontis graphs")
    parser.add_argument("db", nargs="?", help="one database id; default runs all databases")
    parser.add_argument("--train", action="store_true", help="use the BIRD train split")
    parser.add_argument("--force", action="store_true", help="clear selected graphs and preprocess logs first")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    parser.add_argument("--workers", type=int, default=1, help="parallel databases")
    parser.add_argument("--modules", help="comma-separated modules to run explicitly")
    parser.add_argument("--run-id", help="preprocess run id")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--static-only",
        "--no-ai",
        dest="static_only",
        action="store_true",
        help="run deterministic schema/statistics/official-description modules only",
    )
    modes.add_argument(
        "--ai-only",
        action="store_true",
        help="run explorer agents and semantic embedding, without deterministic modules",
    )
    modes.add_argument(
        "--agent-only",
        action="store_true",
        help="run explorer agents only, without deterministic modules or embedding",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    parsed = parser.parse_args(argv)
    if parsed.workers < 1:
        parser.error("--workers must be at least 1")
    if parsed.modules and (parsed.static_only or parsed.ai_only or parsed.agent_only):
        parser.error("--modules cannot be combined with a pipeline mode")
    if parsed.run_id:
        set_run_id(parsed.run_id)

    workers = parsed.workers
    selected_modules = (
        [name.strip() for name in parsed.modules.split(",") if name.strip()]
        if parsed.modules
        else None
    )
    db_filter = parsed.db

    no_ai = parsed.static_only
    ai_only = parsed.ai_only
    agent_only = parsed.agent_only
    force = parsed.force
    debug = parsed.debug
    train = parsed.train

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
        mode = "agent only"
    elif ai_only:
        mode = "agent + embedding"
    else:
        mode = "static + agent + embedding"
    split = "train" if train else "dev"
    print(f"=== BIRD Extract ({split}, {mode}) ===")
    print(f"Databases: {len(db_dirs)}\n")
    print(f"Workers: {workers}")
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
    total_static = 0.0
    total_agent = total_relation_review = total_business_profile = 0.0
    total_desc_review = total_desc_audit = total_disambig = total_readme = 0.0
    total_embedding = 0.0
    total_preprocess_llm_input_tokens = 0
    total_preprocess_llm_cached_input_tokens = 0
    total_preprocess_llm_uncached_input_tokens = 0
    total_preprocess_llm_output_tokens = 0
    total_preprocess_llm_tokens = 0
    total_preprocess_embedding_tokens = 0
    total_preprocess_tokens = 0

    def run_db(db_dir: Path) -> dict:
        name = db_dir.name
        if selected_modules:
            from scripts.preprocess_engine import RunOptions, init_workspace, run_modules

            pontis_dir = get_preprocess_dir(name, train)
            if force and pontis_dir.exists():
                shutil.rmtree(pontis_dir)
                logger.info(f"  已删除旧 preprocess 输出: {name}")
            workspace, config = init_workspace(str(db_dir), verbose=debug)
            if force:
                workspace.clear_graph()
                logger.info(f"  已清空 Neo4j 图谱: {name}")
            pontis_dir.mkdir(parents=True, exist_ok=True)
            result = {
                "name": name,
                "static": 0.0,
                "agent": 0.0,
                "schema_prepare": 0.0,
                "relation_review": 0.0,
                "business_profile": 0.0,
                "description_review": 0.0,
                "description_audit": 0.0,
                "disambiguate": 0.0,
                "readme": 0.0,
                "embedding": 0.0,
                "preprocess_llm_calls": 0,
                "preprocess_llm_input_tokens": 0,
                "preprocess_llm_cached_input_tokens": 0,
                "preprocess_llm_uncached_input_tokens": 0,
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
                    options=RunOptions(
                        continue_on_error=False,
                        collect_timing=True,
                        module_kwargs={
                            "db_column_domain": {
                                "strategy": "pairwise_filter",
                                "strategy_options": BIRD_COLUMN_DOMAIN_OPTIONS,
                            },
                        },
                    ),
                )
                result["schema_prepare"] = timings.get("agent_schema_prepare", 0.0)
                result["agent"] = result["schema_prepare"]
                result["relation_review"] = timings.get("agent_column_domain_review", 0.0)
                result["business_profile"] = timings.get("agent_bird_profile", 0.0)
                result["description_review"] = timings.get("bird_official_description_extract", 0.0)
                result["description_audit"] = timings.get("agent_description_audit", 0.0)
                result["disambiguate"] = timings.get("agent_disambiguate", 0.0)
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
        )

    def record_result(result: dict) -> None:
        nonlocal total_static
        nonlocal total_agent, total_relation_review, total_business_profile
        nonlocal total_desc_review, total_desc_audit, total_disambig, total_readme
        nonlocal total_embedding
        nonlocal total_preprocess_llm_input_tokens, total_preprocess_llm_cached_input_tokens
        nonlocal total_preprocess_llm_uncached_input_tokens, total_preprocess_llm_output_tokens
        nonlocal total_preprocess_llm_tokens, total_preprocess_embedding_tokens
        nonlocal total_preprocess_tokens
        total_static += result["static"]
        total_agent += result["agent"]
        total_relation_review += result.get("relation_review", 0.0)
        total_business_profile += result.get("business_profile", 0.0)
        total_desc_review += result.get("description_review", 0.0)
        total_desc_audit += result.get("description_audit", 0.0)
        total_disambig += result["disambiguate"]
        total_readme += result["readme"]
        total_embedding += result["embedding"]
        total_preprocess_llm_input_tokens += int(result.get("preprocess_llm_input_tokens", 0) or 0)
        total_preprocess_llm_cached_input_tokens += int(result.get("preprocess_llm_cached_input_tokens", 0) or 0)
        total_preprocess_llm_uncached_input_tokens += int(result.get("preprocess_llm_uncached_input_tokens", 0) or 0)
        total_preprocess_llm_output_tokens += int(result.get("preprocess_llm_output_tokens", 0) or 0)
        total_preprocess_llm_tokens += int(result.get("preprocess_llm_total_tokens", 0) or 0)
        total_preprocess_embedding_tokens += int(result.get("preprocess_embedding_total_tokens", 0) or 0)
        total_preprocess_tokens += int(result.get("preprocess_total_tokens", 0) or 0)
        all_results.append(result)

    def format_parts(result: dict) -> str:
        parts = []
        if result["static"]:
            parts.append(f"Static: {result['static']:.1f}s")
        if result["agent"]:
            parts.append(f"Schema: {result['agent']:.1f}s")
        if result.get("relation_review"):
            parts.append(f"Rel/Disambig Review: {result['relation_review']:.1f}s")
        if result.get("business_profile"):
            parts.append(f"Business Profile: {result['business_profile']:.1f}s")
        if result.get("description_review"):
            parts.append(f"Official Description: {result['description_review']:.1f}s")
        if result.get("description_audit"):
            parts.append(f"Description Audit: {result['description_audit']:.1f}s")
        if result["disambiguate"]:
            parts.append(f"Disambig: {result['disambiguate']:.1f}s")
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
        total_static + total_agent + total_relation_review + total_business_profile + total_desc_review
        + total_desc_audit + total_disambig + total_readme + total_embedding
    )
    print(
        f"Time: static {total_static:.1f}s, "
        f"schema {total_agent:.1f}s, rel/disambig review {total_relation_review:.1f}s, "
        f"business profile {total_business_profile:.1f}s, "
        f"official description {total_desc_review:.1f}s, "
        f"description audit {total_desc_audit:.1f}s, "
        f"disambig {total_disambig:.1f}s, "
        f"readme {total_readme:.1f}s, "
        f"embedding {total_embedding:.1f}s, "
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
            "schema": total_agent,
            "relation_review": total_relation_review,
            "business_profile": total_business_profile,
            "description_review": total_desc_review,
            "description_audit": total_desc_audit,
            "disambiguate": total_disambig,
            "readme": total_readme,
            "embedding": total_embedding,
            "total": total_all,
        },
        "preprocess_tokens": {
            "llm_input_tokens": total_preprocess_llm_input_tokens,
            "llm_cached_input_tokens": total_preprocess_llm_cached_input_tokens,
            "llm_uncached_input_tokens": total_preprocess_llm_uncached_input_tokens,
            "llm_output_tokens": total_preprocess_llm_output_tokens,
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
        sys.exit(1)


if __name__ == "__main__":
    main()
