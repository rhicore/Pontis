#!/usr/bin/env python3
"""KDD Cup Docker entrypoint runner for Pontis.

The official evaluator mounts:
  /input   read-only task directories
  /output  writable predictions
  /logs    writable runtime logs

This runner gives every task a fixed, non-reused Neo4j Bolt port and a unique
Neo4j runtime directory. Concurrency only controls how many task-specific
Neo4j processes run at the same time.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.KDDCUP.extract_public import extract_one
from scripts.KDDCUP.test_public import TaskInfo, run_task, write_run_summary


DEFAULT_TEAM_ID = "team1569"
DEFAULT_VERSION = "v1"
DEFAULT_NEO4J_PASSWORD = "pontis_kdd_neo4j"
DEFAULT_BOLT_BASE = 7700
DEFAULT_HTTP_BASE = 7474

logger = logging.getLogger("kdd_submission")


def _task_sort_key(name: str) -> tuple[int, str]:
    try:
        return (int(name.split("_", 1)[1]), name)
    except (IndexError, ValueError):
        return (10**9, name)


def configure_model_env() -> None:
    if os.environ.get("MODEL_API_URL"):
        os.environ["OPENAI_BASE_URL"] = os.environ["MODEL_API_URL"]
    if os.environ.get("MODEL_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["MODEL_API_KEY"]
    if os.environ.get("MODEL_NAME"):
        os.environ["PONTIS_AGENT_MODEL"] = os.environ["MODEL_NAME"]
    os.environ.setdefault("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD)


def load_task_meta(task_dir: Path) -> dict:
    path = task_dir / "task.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def discover_tasks(input_root: Path, selected: list[str], limit: int | None) -> list[Path]:
    wanted = set()
    for item in selected:
        for part in item.split(","):
            part = part.strip()
            if part:
                wanted.add(part if part.startswith("task_") else f"task_{part}")

    tasks = []
    for task_dir in sorted(input_root.glob("task_*"), key=lambda p: _task_sort_key(p.name)):
        if not task_dir.is_dir():
            continue
        if wanted and task_dir.name not in wanted:
            continue
        tasks.append(task_dir)
        if limit and len(tasks) >= limit:
            break
    return tasks


def copy_task_to_workdir(input_task_dir: Path, work_task_dir: Path) -> None:
    if work_task_dir.exists():
        shutil.rmtree(work_task_dir)
    work_task_dir.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(input_task_dir):
        dirs[:] = [d for d in dirs if d != ".pontis"]
        src_root = Path(root)
        rel_root = src_root.relative_to(input_task_dir)
        dst_root = work_task_dir / rel_root
        dst_root.mkdir(parents=True, exist_ok=True)
        for filename in files:
            src = src_root / filename
            dst = dst_root / filename
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)


def write_task_config(config_path: Path, task_id: str, work_task_dir: Path, bolt_port: int) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join([
            "projects:",
            f"  {task_id}:",
            "    source:",
            "      type: fs",
            f"      path: {work_task_dir}",
            "    graph:",
            "      database: neo4j",
            "      user: neo4j",
            "      password_env: NEO4J_PASSWORD",
            f"      uri: bolt://127.0.0.1:{bolt_port}",
            "",
        ]),
        encoding="utf-8",
    )


def run_instance_command(
    command: str,
    *,
    task_id: str,
    config_path: Path,
    neo4j_base_dir: Path,
    heap_initial: str,
    heap_max: str,
    pagecache: str,
    extra_env: dict[str, str],
) -> None:
    cmd = [
        sys.executable,
        "-m",
        "storage.neo4j.instances",
        command,
        task_id,
        "--config",
        str(config_path),
        "--base-dir",
        str(neo4j_base_dir),
        "--heap-initial",
        heap_initial,
        "--heap-max",
        heap_max,
        "--pagecache",
        pagecache,
        "--start-grace",
        "4",
    ]
    subprocess.run(cmd, cwd=str(REPO_ROOT), env=extra_env, check=True)


def wait_for_neo4j(uri: str, password: str, timeout_seconds: float) -> None:
    from neo4j import GraphDatabase

    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        driver = None
        try:
            driver = GraphDatabase.driver(uri, auth=("neo4j", password))
            with driver.session(database="neo4j") as session:
                session.run("RETURN 1 AS ok").consume()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.0)
        finally:
            if driver is not None:
                driver.close()
    raise TimeoutError(f"Neo4j did not become ready at {uri}: {last_error}")


def write_fallback_prediction(output_root: Path, task_id: str) -> None:
    out_dir = output_root / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "prediction.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["answer"])


def copy_task_logs(work_task_dir: Path, task_log_dir: Path) -> None:
    pontis_log = work_task_dir / ".pontis" / "kdd_extract.log"
    if pontis_log.exists():
        shutil.copy2(pontis_log, task_log_dir / "kdd_extract.log")


def run_single_task(args) -> int:
    configure_model_env()
    input_task_dir = Path(args.input_task_dir).resolve()
    output_root = Path(args.output_root).resolve()
    logs_root = Path(args.logs_root).resolve()
    task_id = args.task_id or input_task_dir.name
    bolt_port = int(args.bolt_port)
    uri = f"bolt://127.0.0.1:{bolt_port}"

    task_log_dir = logs_root / "tasks" / task_id
    work_task_dir = logs_root / "work" / task_id
    config_path = logs_root / "configs" / f"{task_id}.pontis.yml"
    neo4j_base_dir = logs_root / "neo4j"
    task_log_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PONTIS_CONFIG_PATH"] = str(config_path)
    env.setdefault("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD)

    result = None
    try:
        copy_task_to_workdir(input_task_dir, work_task_dir)
        write_task_config(config_path, task_id, work_task_dir, bolt_port)
        os.environ["PONTIS_CONFIG_PATH"] = str(config_path)

        logger.info("[%s] start Neo4j %s", task_id, uri)
        run_instance_command(
            "start",
            task_id=task_id,
            config_path=config_path,
            neo4j_base_dir=neo4j_base_dir,
            heap_initial=args.neo4j_heap_initial,
            heap_max=args.neo4j_heap_max,
            pagecache=args.neo4j_pagecache,
            extra_env=env,
        )
        wait_for_neo4j(uri, env["NEO4J_PASSWORD"], args.neo4j_ready_timeout)

        logger.info("[%s] extract", task_id)
        extract_result = extract_one(
            work_task_dir,
            force=True,
            with_agent=True,
            with_ai_db=True,
            with_db_agent=True,
            with_embedding=False,
            max_json_pattern_mb=args.max_json_pattern_mb,
            clear_task_graph=False,
            smoke_only=False,
            debug=args.debug,
        )
        (task_log_dir / "extract_result.json").write_text(
            json.dumps(asdict(extract_result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        copy_task_logs(work_task_dir, task_log_dir)

        meta = load_task_meta(work_task_dir)
        task = TaskInfo(
            task_id=str(meta.get("task_id") or task_id),
            difficulty=str(meta.get("difficulty") or ""),
            question=str(meta.get("question") or ""),
            task_dir=work_task_dir,
        )

        logger.info("[%s] solve", task_id)
        result = run_task(
            task,
            public_root=Path("/"),
            run_dir=task_log_dir,
            prediction_root=output_root,
            max_rounds=args.max_rounds,
            effort=args.effort,
            extract_first=False,
            reflection=False,
            penalty_lambda=0.1,
            repair_low_score=0,
            repair_threshold=0.999,
        )
        if result.error or result.parse_error:
            logger.error("[%s] task issue: error=%s parse=%s", task_id, result.error, result.parse_error)
        if not (output_root / task_id / "prediction.csv").exists():
            write_fallback_prediction(output_root, task_id)
        return 0 if result and not result.error else 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] failed: %s", task_id, exc)
        write_fallback_prediction(output_root, task_id)
        return 1
    finally:
        try:
            run_instance_command(
                "stop",
                task_id=task_id,
                config_path=config_path,
                neo4j_base_dir=neo4j_base_dir,
                heap_initial=args.neo4j_heap_initial,
                heap_max=args.neo4j_heap_max,
                pagecache=args.neo4j_pagecache,
                extra_env=env,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Neo4j stop failed: %s", task_id, exc)
        copy_task_logs(work_task_dir, task_log_dir)
        if not args.keep_workdirs:
            shutil.rmtree(work_task_dir, ignore_errors=True)
        if not args.keep_neo4j_dirs:
            shutil.rmtree(neo4j_base_dir / task_id, ignore_errors=True)


def run_task_subprocess(task_dir: Path, idx: int, args) -> dict:
    task_id = task_dir.name
    bolt_port = args.bolt_base + idx
    task_log_dir = Path(args.logs_root).resolve() / "tasks" / task_id
    task_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = task_log_dir / "submission.log"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--single-task",
        "--input-task-dir",
        str(task_dir),
        "--task-id",
        task_id,
        "--bolt-port",
        str(bolt_port),
        "--output-root",
        str(Path(args.output_root).resolve()),
        "--logs-root",
        str(Path(args.logs_root).resolve()),
        "--max-rounds",
        str(args.max_rounds),
        "--effort",
        args.effort,
        "--max-json-pattern-mb",
        str(args.max_json_pattern_mb),
        "--neo4j-heap-initial",
        args.neo4j_heap_initial,
        "--neo4j-heap-max",
        args.neo4j_heap_max,
        "--neo4j-pagecache",
        args.neo4j_pagecache,
        "--neo4j-ready-timeout",
        str(args.neo4j_ready_timeout),
    ]
    if args.keep_workdirs:
        cmd.append("--keep-workdirs")
    if args.keep_neo4j_dirs:
        cmd.append("--keep-neo4j-dirs")
    if args.debug:
        cmd.append("--debug")

    env = dict(os.environ)
    configure_model_env()
    env.update({
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", ""),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "PONTIS_AGENT_MODEL": os.environ.get("PONTIS_AGENT_MODEL", ""),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD),
    })
    started = time.time()
    with log_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            timeout=args.task_timeout_seconds,
        )
    prediction = Path(args.output_root).resolve() / task_id / "prediction.csv"
    if not prediction.exists():
        write_fallback_prediction(Path(args.output_root).resolve(), task_id)
    return {
        "task_id": task_id,
        "index": idx,
        "bolt_port": bolt_port,
        "returncode": proc.returncode,
        "seconds": round(time.time() - started, 3),
        "prediction": str(prediction),
        "log": str(log_path),
    }


def cleanup_timed_out_task(task_id: str, args) -> None:
    logs_root = Path(args.logs_root).resolve()
    config_path = logs_root / "configs" / f"{task_id}.pontis.yml"
    if config_path.exists():
        try:
            run_instance_command(
                "stop",
                task_id=task_id,
                config_path=config_path,
                neo4j_base_dir=logs_root / "neo4j",
                heap_initial=args.neo4j_heap_initial,
                heap_max=args.neo4j_heap_max,
                pagecache=args.neo4j_pagecache,
                extra_env=dict(os.environ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] timeout cleanup stop failed: %s", task_id, exc)
    if not args.keep_workdirs:
        shutil.rmtree(logs_root / "work" / task_id, ignore_errors=True)
    if not args.keep_neo4j_dirs:
        shutil.rmtree(logs_root / "neo4j" / task_id, ignore_errors=True)


def run_all_tasks(args) -> int:
    configure_model_env()
    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    logs_root = Path(args.logs_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)

    tasks = discover_tasks(input_root, args.task, args.limit)
    if not tasks:
        raise SystemExit(f"No task_* directories found under {input_root}")

    assignments = {task.name: idx for idx, task in enumerate(tasks)}
    (logs_root / "port_assignments.json").write_text(
        json.dumps(
            {task_id: {"bolt": args.bolt_base + idx, "http": args.http_base + (args.bolt_base + idx - 7687)}
             for task_id, idx in assignments.items()},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    logger.info("tasks=%d workers=%d output=%s logs=%s", len(tasks), args.task_workers, output_root, logs_root)
    results = []
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.task_workers)) as pool:
        futures = {
            pool.submit(run_task_subprocess, task, assignments[task.name], args): task.name
            for task in tasks
        }
        for future in as_completed(futures):
            task_id = futures[future]
            try:
                row = future.result()
            except subprocess.TimeoutExpired as exc:
                failures += 1
                cleanup_timed_out_task(task_id, args)
                write_fallback_prediction(output_root, task_id)
                row = {
                    "task_id": task_id,
                    "returncode": 124,
                    "seconds": args.task_timeout_seconds,
                    "prediction": str(output_root / task_id / "prediction.csv"),
                    "log": str(logs_root / "tasks" / task_id / "submission.log"),
                    "error": f"timeout after {exc.timeout}s",
                }
            except Exception as exc:  # noqa: BLE001
                failures += 1
                write_fallback_prediction(output_root, task_id)
                row = {
                    "task_id": task_id,
                    "returncode": 1,
                    "seconds": 0,
                    "prediction": str(output_root / task_id / "prediction.csv"),
                    "log": str(logs_root / "tasks" / task_id / "submission.log"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            else:
                if row.get("returncode"):
                    failures += 1
            results.append(row)
            status = "OK" if not row.get("returncode") else "FAILED"
            logger.info("[%s] %s %.1fs port=%s", task_id, status, row.get("seconds", 0), row.get("bolt_port", "-"))

    results.sort(key=lambda item: _task_sort_key(item["task_id"]))
    (logs_root / "submission_summary.json").write_text(
        json.dumps({"total": len(results), "failures": failures, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", default="/input")
    parser.add_argument("--output-root", default="/output")
    parser.add_argument("--logs-root", default="/logs")
    parser.add_argument("--team-id", default=DEFAULT_TEAM_ID)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--task-workers", type=int, default=4)
    parser.add_argument("--task-timeout-seconds", type=float, default=1800)
    parser.add_argument("--bolt-base", type=int, default=DEFAULT_BOLT_BASE)
    parser.add_argument("--http-base", type=int, default=DEFAULT_HTTP_BASE)
    parser.add_argument("--max-rounds", type=int, default=80)
    parser.add_argument("--effort", default="max", choices=["mid", "high", "max"])
    parser.add_argument("--max-json-pattern-mb", type=float, default=64.0)
    parser.add_argument("--neo4j-heap-initial", default="256m")
    parser.add_argument("--neo4j-heap-max", default="768m")
    parser.add_argument("--neo4j-pagecache", default="128m")
    parser.add_argument("--neo4j-ready-timeout", type=float, default=120)
    parser.add_argument("--keep-workdirs", action="store_true")
    parser.add_argument("--keep-neo4j-dirs", action="store_true")
    parser.add_argument("--debug", action="store_true")

    parser.add_argument("--single-task", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--input-task-dir", help=argparse.SUPPRESS)
    parser.add_argument("--task-id", help=argparse.SUPPRESS)
    parser.add_argument("--bolt-port", type=int, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.single_task:
        if not args.input_task_dir or not args.bolt_port:
            raise SystemExit("--single-task requires --input-task-dir and --bolt-port")
        return run_single_task(args)
    return run_all_tasks(args)


if __name__ == "__main__":
    raise SystemExit(main())
