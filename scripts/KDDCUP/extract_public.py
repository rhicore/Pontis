#!/usr/bin/env python3
"""Extract and smoke-test KDDCup public tasks with Pontis.

Default behavior runs the full KDD extraction stack:
- materialize storage facts for each task directory
- run static extractors for JSON/CSV/DB structures
- run agent explorers for JSON/CSV/text summaries
- run LLM DB summaries and DB-oriented agents when DB files exist
- run storage/tool smoke tests

Embedding remains opt-in because it is a separate indexing phase.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from extractor.engine import RunOptions, file_log_handler, get_registry, init_workspace, run_modules
from tool.grep import grep_command
from tool.jd import jd_command
from tool.query import query_command
from tool.read import read_command
from tool.utils.workspace_access import resolve_file_sources

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_ROOT = REPO_ROOT / "example_data" / "KDDCUP" / "public"
DEFAULT_MAX_JSON_PATTERN_MB = 64.0

STATIC_PIPELINE = [
    "json_pattern",
    "csv_column_stats",
    "db_column_stats_approx",
    "db_fk_validate",
    "db_column_overlap",
]

AGENT_PIPELINE = [
    "agent_json_pattern_summary",
    "agent_text_chunk",
    "agent_csv_summary",
]

EMBEDDING_PIPELINE = [
    "semantic_embedding",
]


@dataclass
class SmokeResult:
    ok: bool = True
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def pass_(self, name: str) -> None:
        self.checks.append(name)

    def fail(self, name: str, message: str) -> None:
        self.ok = False
        self.failures.append(f"{name}: {message}")


@dataclass
class TaskResult:
    task_id: str
    difficulty: str = ""
    seconds: float = 0.0
    files: dict[str, int] = field(default_factory=dict)
    modules: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    smoke: SmokeResult = field(default_factory=SmokeResult)
    error: str = ""


@dataclass
class DataProfile:
    counts: dict[str, int] = field(default_factory=dict)
    files: dict[str, list[Path]] = field(default_factory=dict)
    large_files: list[tuple[str, int]] = field(default_factory=list)

    def has(self, *exts: str) -> bool:
        return any(self.counts.get(ext, 0) > 0 for ext in exts)

    @property
    def docs(self) -> list[Path]:
        return list(self.files.get("md", []))


def _load_task_json(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "task.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _task_dirs(public_root: Path, tasks: list[str], difficulty: str | None, limit: int | None) -> list[Path]:
    input_root = public_root / "input"
    if not input_root.exists():
        raise FileNotFoundError(f"KDD public input root not found: {input_root}")

    selected = []
    wanted = set()
    for item in tasks:
        for part in item.split(","):
            part = part.strip()
            if not part:
                continue
            wanted.add(part if part.startswith("task_") else f"task_{part}")

    for task_dir in sorted(input_root.glob("task_*"), key=lambda p: _task_sort_key(p.name)):
        if not task_dir.is_dir():
            continue
        if wanted and task_dir.name not in wanted:
            continue
        meta = _load_task_json(task_dir)
        if difficulty and meta.get("difficulty") != difficulty:
            continue
        selected.append(task_dir)
        if limit and len(selected) >= limit:
            break
    return selected


def _task_sort_key(name: str) -> tuple[int, str]:
    try:
        return (int(name.split("_", 1)[1]), name)
    except (IndexError, ValueError):
        return (10**9, name)


def _available_pipeline(names: list[str]) -> list[str]:
    registry = get_registry()
    return [name for name in names if name in registry]


def _sum_timings(timings: dict[str, float], names: list[str]) -> float:
    return sum(timings.get(name, 0.0) for name in names)


def extract_one(
    task_dir: Path,
    *,
    force: bool,
    with_agent: bool,
    with_ai_db: bool,
    with_db_agent: bool,
    with_embedding: bool,
    max_json_pattern_mb: float,
    clear_task_graph: bool,
    smoke_only: bool,
    debug: bool,
) -> TaskResult:
    task_dir = task_dir.resolve()
    task_meta = _load_task_json(task_dir)
    task_id = task_meta.get("task_id") or task_dir.name
    result = TaskResult(task_id=task_id, difficulty=task_meta.get("difficulty", ""))
    profile = _profile_task_dir(task_dir)
    result.files = dict(profile.counts)
    t0 = time.time()

    pontis_dir = task_dir / ".pontis"
    if force and pontis_dir.exists():
        shutil.rmtree(pontis_dir)

    workspace, config = init_workspace(str(task_dir), verbose=debug)
    pontis_dir.mkdir(exist_ok=True)

    with file_log_handler(str(pontis_dir / "kdd_extract.log")):
        logger.info("=== %s ===", task_id)
        logger.info("question: %s", task_meta.get("question", ""))

        if not smoke_only:
            if clear_task_graph:
                deleted = _clear_task_graph(workspace, profile)
                logger.info("cleared task graph nodes: %s", deleted)

            workspace.refresh_sources()

            static_pipeline = _available_pipeline(_static_pipeline_for(profile))
            result.modules.extend(static_pipeline)
            static_timings = _run_static_pipeline(
                static_pipeline,
                workspace,
                config=config,
                max_json_pattern_mb=max_json_pattern_mb,
            )
            result.timings.update(static_timings)
            logger.info("static done: %.1fs", _sum_timings(static_timings, static_pipeline))

            if with_agent:
                agent_pipeline = _available_pipeline(_agent_pipeline_for(profile))
                result.modules.extend(agent_pipeline)
                agent_timings = run_modules(
                    agent_pipeline,
                    workspace,
                    config=config,
                    options=RunOptions(continue_on_error=True, collect_timing=True),
                )
                result.timings.update(agent_timings)
                logger.info("agent done: %.1fs", _sum_timings(agent_timings, agent_pipeline))
                text_timings = _run_text_chunk_agents(workspace, task_dir, profile)
                result.timings.update(text_timings)
                if text_timings:
                    result.modules.append("agent_text_chunk")

            if with_ai_db:
                ai_db_pipeline = _available_pipeline(_ai_db_pipeline_for(profile))
                result.modules.extend(ai_db_pipeline)
                ai_db_timings = run_modules(
                    ai_db_pipeline,
                    workspace,
                    config=config,
                    options=RunOptions(continue_on_error=True, collect_timing=True),
                )
                result.timings.update(ai_db_timings)
                logger.info("ai db done: %.1fs", _sum_timings(ai_db_timings, ai_db_pipeline))

            if with_db_agent:
                db_agent_pipeline = _available_pipeline(_db_agent_pipeline_for(profile))
                result.modules.extend(db_agent_pipeline)
                db_agent_timings = run_modules(
                    db_agent_pipeline,
                    workspace,
                    config=config,
                    options=RunOptions(continue_on_error=True, collect_timing=True),
                )
                result.timings.update(db_agent_timings)
                logger.info("db agent done: %.1fs", _sum_timings(db_agent_timings, db_agent_pipeline))

            if with_embedding:
                embedding_pipeline = _available_pipeline(EMBEDDING_PIPELINE)
                embedding_timings = run_modules(
                    embedding_pipeline,
                    workspace,
                    config=config,
                    options=RunOptions(continue_on_error=True, collect_timing=True),
                )
                result.timings.update(embedding_timings)
                logger.info("embedding done: %.1fs", _sum_timings(embedding_timings, embedding_pipeline))

        result.smoke = smoke_test(workspace)
        logger.info("smoke: %s", "ok" if result.smoke.ok else "failed")
        for failure in result.smoke.failures:
            logger.info("  failure: %s", failure)

    result.seconds = time.time() - t0
    return result


def _profile_task_dir(task_dir: Path) -> DataProfile:
    profile = DataProfile()
    for path in task_dir.rglob("*"):
        if not path.is_file() or ".pontis" in path.parts:
            continue
        ext = path.suffix.lower().lstrip(".") or "[no_ext]"
        profile.counts[ext] = profile.counts.get(ext, 0) + 1
        profile.files.setdefault(ext, []).append(path)
        size = path.stat().st_size
        if size >= 20 * 1024 * 1024:
            profile.large_files.append((str(path.relative_to(task_dir)), size))
    return profile


def _clear_task_graph(workspace, profile: DataProfile) -> int:
    """Delete graph facts derived from this task's relative file paths.

    Current storage facts are not fully project-scoped yet, so this is the
    practical way to rerun one KDD task from a clean graph state without
    dropping the entire Neo4j database.
    """
    file_paths = sorted({
        str(path.relative_to(Path(workspace.project_path)))
        for paths in profile.files.values()
        for path in paths
    })
    db_paths = sorted(str(path.relative_to(Path(workspace.project_path))) for path in profile.files.get("db", []))
    csv_paths = sorted({
        str(path.relative_to(Path(workspace.project_path)))
        for ext in ("csv", "tsv")
        for path in profile.files.get(ext, [])
    })

    rows = workspace.cypher(
        """
        MATCH (n)
        WHERE n.path IN $file_paths
           OR (n:pattern AND n.source_path IN $file_paths)
           OR n._db_ref IN $db_paths
           OR n._ref IN $file_paths
           OR any(csv_path IN $csv_paths WHERE n._ref STARTS WITH csv_path + '--')
        WITH collect(DISTINCT n) AS direct_nodes
        OPTIONAL MATCH (f:file)--(p:pattern)
        WHERE f.path IN $file_paths
        WITH direct_nodes, collect(DISTINCT p) AS pattern_nodes
        OPTIONAL MATCH (f:file)--(c:chunk)
        WHERE f.path IN $file_paths
        WITH direct_nodes, pattern_nodes, collect(DISTINCT c) AS chunk_nodes
        WITH [node IN direct_nodes + pattern_nodes + chunk_nodes WHERE node IS NOT NULL] AS nodes
        FOREACH (node IN nodes | DETACH DELETE node)
        RETURN size(nodes) AS deleted
        """,
        params={
            "file_paths": file_paths,
            "db_paths": db_paths,
            "csv_paths": csv_paths,
        },
    )
    return int(rows[0].get("deleted", 0)) if rows else 0


def _static_pipeline_for(profile: DataProfile) -> list[str]:
    pipeline: list[str] = []
    if profile.has("json"):
        pipeline.append("json_pattern")
    if profile.has("csv", "tsv"):
        pipeline.append("csv_column_stats")
    if profile.has("db", "sqlite", "sqlite3", "duckdb"):
        pipeline.extend([
            "db_column_stats_approx",
            "db_fk_validate",
            "db_column_overlap",
        ])
    return pipeline


def _run_static_pipeline(
    pipeline: list[str],
    workspace,
    *,
    config,
    max_json_pattern_mb: float,
) -> dict[str, float]:
    timings: dict[str, float] = {}
    remaining = list(pipeline)

    if "json_pattern" in remaining:
        from extractor.modules.json_pattern import generate as json_pattern_generate

        t0 = time.time()
        max_file_size = None
        if max_json_pattern_mb and max_json_pattern_mb > 0:
            max_file_size = int(max_json_pattern_mb * 1024 * 1024)
        json_pattern_generate(workspace, max_file_size=max_file_size)
        timings["json_pattern"] = time.time() - t0
        remaining = [name for name in remaining if name != "json_pattern"]

    timings.update(run_modules(
        remaining,
        workspace,
        config=config,
        options=RunOptions(continue_on_error=True, collect_timing=True),
    ))
    return timings


def _agent_pipeline_for(profile: DataProfile) -> list[str]:
    pipeline: list[str] = []
    if profile.has("json"):
        pipeline.append("agent_json_pattern_summary")
    if profile.has("csv", "tsv"):
        pipeline.append("agent_csv_summary")
    return pipeline


def _ai_db_pipeline_for(profile: DataProfile) -> list[str]:
    if not profile.has("db", "sqlite", "sqlite3", "duckdb"):
        return []
    return ["ai_db_column_summary", "ai_db_table_summary", "ai_db_summary"]


def _db_agent_pipeline_for(profile: DataProfile) -> list[str]:
    if not profile.has("db", "sqlite", "sqlite3", "duckdb"):
        return []
    return ["agent_analyze", "agent_join_detect", "agent_disambiguate", "agent_readme"]


def _run_text_chunk_agents(workspace, task_dir: Path, profile: DataProfile) -> dict[str, float]:
    """Run text chunk explorer only for Markdown docs, not every :text file."""
    docs = [
        path for path in profile.docs
        if path.name.lower() == "knowledge.md" or "/context/doc/" in "/" + str(path.relative_to(task_dir))
    ]
    if not docs:
        return {}

    from explorer.text_chunk import generate as text_chunk_generate

    start = time.time()
    for path in sorted(docs):
        rel_path = str(path.relative_to(task_dir))
        try:
            text_chunk_generate(workspace, file=rel_path, min_chars=0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_text_chunk failed for %s: %s: %s", rel_path, type(exc).__name__, exc)
    return {"agent_text_chunk": time.time() - start}


def smoke_test(workspace) -> SmokeResult:
    result = SmokeResult()
    try:
        workspace.refresh_sources()
    except Exception as exc:
        result.fail("refresh_sources", f"{type(exc).__name__}: {exc}")
        return result

    counts = _label_counts(workspace)
    result.counts = counts
    if counts.get("file", 0) <= 0:
        result.fail("file_count", "no file nodes found")
    else:
        result.pass_("file_count")

    _check_json(workspace, result)
    _check_text(workspace, result)
    _check_csv(workspace, result)
    _check_db(workspace, result)
    return result


def _label_counts(workspace) -> dict[str, int]:
    all_sources = resolve_file_sources(workspace, ".", allow_directory=True)
    by_label = {
        label: [src for src in all_sources if label in src.labels]
        for label in ["json", "csv", "tsv", "db", "text", "md"]
    }
    counts = {"file": len(all_sources)}
    counts.update({label: len(items) for label, items in by_label.items()})

    csv_paths = [src.path for src in by_label["csv"] + by_label["tsv"]]
    if csv_paths:
        rows = workspace.cypher(
            "MATCH (f:file)--(c:col) WHERE f.path IN $paths RETURN count(c) AS c",
            params={"paths": csv_paths},
        )
        counts["col"] = int(rows[0].get("c", 0)) if rows else 0
    else:
        counts["col"] = 0

    json_paths = [src.path for src in by_label["json"]]
    if json_paths:
        rows = workspace.cypher(
            "MATCH (f:file)--(n:pattern) WHERE f.path IN $paths RETURN count(DISTINCT n) AS c",
            params={"paths": json_paths},
        )
        counts["pattern"] = int(rows[0].get("c", 0)) if rows else 0
    else:
        counts["pattern"] = 0

    text_paths = [src.path for src in by_label["text"] + by_label["md"]]
    if text_paths:
        rows = workspace.cypher(
            "MATCH (f:file)--(n:chunk) WHERE f.path IN $paths RETURN count(DISTINCT n) AS c",
            params={"paths": text_paths},
        )
        counts["chunk"] = int(rows[0].get("c", 0)) if rows else 0
    else:
        counts["chunk"] = 0
    return counts


def _first_path(workspace, label: str) -> str | None:
    sources = resolve_file_sources(workspace, ".", labels=(label,), allow_directory=True)
    if not sources:
        return None
    return sources[0].path


def _check_json(workspace, result: SmokeResult) -> None:
    path = _first_path(workspace, "json")
    if not path:
        return
    out = jd_command(workspace, ref=path, limit=5)
    if out.startswith("Error") or "No JSON" in out:
        result.fail("jd", out[:300])
    else:
        result.pass_("jd")


def _check_text(workspace, result: SmokeResult) -> None:
    path = _first_path(workspace, "md") or _first_path(workspace, "text")
    if not path:
        return
    out = read_command(workspace, ref=path, start_line=1, end_line=5)
    if out.startswith("Error"):
        result.fail("read", out[:300])
    else:
        result.pass_("read")

    grep_out = grep_command(workspace, pattern="the", ref=path, head_limit=3)
    if grep_out.startswith("Error"):
        result.fail("grep", grep_out[:300])
    else:
        result.pass_("grep")


def _check_csv(workspace, result: SmokeResult) -> None:
    path = _first_path(workspace, "csv") or _first_path(workspace, "tsv")
    if not path:
        return
    rows = workspace.cypher(
        "MATCH (f:file)--(c:col) WHERE f.path = $path RETURN count(c) AS c",
        params={"path": path},
    )
    count = int(rows[0].get("c", 0)) if rows else 0
    if count <= 0:
        result.fail("csv_columns", f"no columns linked from {path}")
    else:
        result.pass_("csv_columns")


def _check_db(workspace, result: SmokeResult) -> None:
    path = _first_path(workspace, "db")
    if not path:
        return
    out = query_command(
        workspace,
        ref=path,
        sql="SELECT name FROM sqlite_master WHERE type IN ('table', 'view') LIMIT 5",
        limit=5,
    )
    if out.startswith("错误") or out.startswith("SQL 执行错误"):
        result.fail("query", out[:300])
    else:
        result.pass_("query")


def _write_summary(path: Path, results: list[TaskResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for item in results:
        row = asdict(item)
        row["smoke"] = asdict(item.smoke)
        payload.append(row)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and smoke-test KDDCup public tasks with Pontis.")
    parser.add_argument("--public-root", default=str(DEFAULT_PUBLIC_ROOT), help="Path to example_data/KDDCUP/public")
    parser.add_argument("--task", action="append", default=[], help="Task id, e.g. task_11 or 11. Can repeat or comma-separate.")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard", "extreme"], help="Filter by task difficulty")
    parser.add_argument("--limit", type=int, help="Limit number of selected tasks")
    parser.add_argument("--force", action="store_true", help="Remove task .pontis before extraction")
    parser.add_argument("--with-agent", action="store_true", help="Deprecated: agent summary explorers are enabled by default")
    parser.add_argument("--with-ai-db", action="store_true", help="Deprecated: LLM DB summaries are enabled by default")
    parser.add_argument("--with-db-agent", action="store_true", help="Deprecated: DB-oriented agents are enabled by default")
    parser.add_argument("--no-agent", action="store_true", help="Skip JSON/CSV/text agent summary explorers")
    parser.add_argument("--no-ai-db", action="store_true", help="Skip LLM DB column/table/file summaries")
    parser.add_argument("--no-db-agent", action="store_true", help="Skip DB-oriented agent analyze/join/disambiguate/readme modules")
    parser.add_argument("--with-embedding", action="store_true", help="Run semantic embedding after extraction")
    parser.add_argument(
        "--max-json-pattern-mb",
        type=float,
        default=DEFAULT_MAX_JSON_PATTERN_MB,
        help="Skip json_pattern for JSON files larger than this size. Use 0 for no limit.",
    )
    parser.add_argument(
        "--clear-task-graph",
        action="store_true",
        help="Delete existing graph facts derived from the selected task paths before extraction.",
    )
    parser.add_argument("--smoke-only", action="store_true", help="Skip extractors and only run smoke tests")
    parser.add_argument("--list", action="store_true", help="List selected tasks and exit")
    parser.add_argument("--profile", action="store_true", help="List selected task data profiles and exit")
    parser.add_argument("--summary", help="Write JSON summary path")
    parser.add_argument("--task-workers", type=int, default=1, help="Number of KDD tasks to extract concurrently.")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    public_root = Path(args.public_root).resolve()
    task_dirs = _task_dirs(public_root, args.task, args.difficulty, args.limit)
    if args.list:
        for task_dir in task_dirs:
            meta = _load_task_json(task_dir)
            print(f"{task_dir.name}\t{meta.get('difficulty', '-')}\t{meta.get('question', '')}")
        return

    if args.profile:
        for task_dir in task_dirs:
            meta = _load_task_json(task_dir)
            profile = _profile_task_dir(task_dir)
            static = ",".join(_static_pipeline_for(profile)) or "-"
            agent = ",".join(_agent_pipeline_for(profile) + (["agent_text_chunk"] if profile.docs else [])) or "-"
            print(f"{task_dir.name}\t{meta.get('difficulty', '-')}\tfiles={profile.counts}\tstatic={static}\tagent={agent}")
            for rel, size in profile.large_files:
                print(f"  large {size / 1024 / 1024:.1f}MB {rel}")
        return

    if not task_dirs:
        print("No tasks selected")
        sys.exit(1)

    with_agent = not args.no_agent
    with_ai_db = not args.no_ai_db
    with_db_agent = not args.no_db_agent

    print("=== KDDCup Public Extract ===")
    print(f"public root: {public_root}")
    print(f"tasks: {len(task_dirs)}")
    print(f"agent: {'on' if with_agent else 'off'}")
    print(f"ai db: {'on' if with_ai_db else 'off'}")
    print(f"db agent: {'on' if with_db_agent else 'off'}")
    print(f"embedding: {'on' if args.with_embedding else 'off'}")
    print(f"task workers: {args.task_workers}")
    print()

    results: list[TaskResult] = []
    failed = 0
    task_workers = max(1, int(args.task_workers or 1))

    def run_selected_task(idx: int, task_dir: Path) -> TaskResult:
        print(f"[{idx}/{len(task_dirs)}] {task_dir.name}")
        try:
            result = extract_one(
                task_dir,
                force=args.force,
                with_agent=with_agent,
                with_ai_db=with_ai_db,
                with_db_agent=with_db_agent,
                with_embedding=args.with_embedding,
                max_json_pattern_mb=args.max_json_pattern_mb,
                clear_task_graph=args.clear_task_graph,
                smoke_only=args.smoke_only,
                debug=args.debug,
            )
            status = "OK" if result.smoke.ok else "SMOKE_FAILED"
            modules = ",".join(result.modules) if result.modules else "-"
            print(f"  {status} {result.seconds:.1f}s files={result.files} modules={modules} counts={result.smoke.counts}")
            for failure in result.smoke.failures:
                print(f"    - {failure}")
            return result
        except Exception as exc:
            meta = _load_task_json(task_dir)
            result = TaskResult(
                task_id=meta.get("task_id") or task_dir.name,
                difficulty=meta.get("difficulty", ""),
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.exception("Task failed: %s", task_dir.name)
            print(f"  FAILED {result.error}")
            return result

    if task_workers == 1:
        for idx, task_dir in enumerate(task_dirs, 1):
            results.append(run_selected_task(idx, task_dir))
    else:
        with ThreadPoolExecutor(max_workers=task_workers) as pool:
            futures = {
                pool.submit(run_selected_task, idx, task_dir): task_dir.name
                for idx, task_dir in enumerate(task_dirs, 1)
            }
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda item: _task_sort_key(item.task_id))
    failed = sum(1 for result in results if result.error or not result.smoke.ok)

    summary_path = Path(args.summary).resolve() if args.summary else public_root / ".pontis_extract_summary.json"
    _write_summary(summary_path, results)
    ok = len(results) - failed
    print()
    print(f"summary: {summary_path}")
    print(f"done: {ok} ok, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
