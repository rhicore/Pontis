"""Unified Slurm entrypoint for BIRD extract and benchmark jobs.

Pontis local Neo4j instances listen on localhost ports, so BIRD jobs must run
on the same Slurm node as the persistent Neo4j job. This wrapper reuses or
starts that Neo4j job, waits until selected projects are ready, then submits
the requested extract/benchmark job pinned to the same node.

Examples:
    python -m scripts.BIRD.bird_slurm extract submit -- --workers 6
    python -m scripts.BIRD.bird_slurm benchmark submit -- --workers 20 --db-workers 6
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from storage.config import load_config
from scripts.neo4j_instances import DEFAULT_BASE_DIR, DEFAULT_ENV_FILE


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SLURM_DIR = DEFAULT_BASE_DIR.parent / "slurm"
DEFAULT_NEO4J_JOB_NAME = "pontis-neo4j"

TASKS = {
    "benchmark": {
        "module": "scripts.BIRD.run_bird_benchmark",
        "default_job_name": "bird-benchmark",
        "args_name": "benchmark_args",
        "label": "BIRD benchmark",
        "cpus": 16,
        "mem": "64G",
        "time": "1-00:00:00",
    },
    "extract": {
        "module": "scripts.BIRD.extract",
        "default_job_name": "bird-extract",
        "args_name": "extract_args",
        "label": "BIRD extract",
        "cpus": 48,
        "mem": "192G",
        "time": "24:00:00",
    },
}


def _task_config(args: argparse.Namespace) -> dict:
    return TASKS[args.task]


def _safe_name(name: str, default: str = "bird-job") -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or default


def _quote(parts: list[str | Path]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _task_args(args: argparse.Namespace) -> list[str]:
    values = list(getattr(args, _task_config(args)["args_name"], []))
    if values and values[0] == "--":
        values = values[1:]
    return values


def _benchmark_uses_bird_global(task_args: list[str]) -> bool:
    use_bird = False
    for arg in task_args:
        if arg == "--no-bird-global":
            use_bird = False
        elif arg == "--use-bird-global":
            use_bird = True
    return use_bird


def _benchmark_db_filter(task_args: list[str]) -> list[str]:
    dbs: list[str] = []
    index = 0
    while index < len(task_args):
        arg = task_args[index]
        if arg == "--db" and index + 1 < len(task_args):
            dbs.extend(_split_csv(task_args[index + 1]))
            index += 2
            continue
        if arg.startswith("--db="):
            dbs.extend(_split_csv(arg.split("=", 1)[1]))
        index += 1
    return dbs


def _extract_db_filter(task_args: list[str]) -> list[str]:
    dbs: list[str] = []
    index = 0
    skip_value_for = {"--run-id", "--workers", "--column-workers", "--modules"}
    while index < len(task_args):
        arg = task_args[index]
        if arg in skip_value_for:
            index += 2
            continue
        if arg.startswith("--run-id="):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        dbs.extend(_split_csv(arg))
        break
    return dbs


def _configured_bird_dev_projects(config_path: Path) -> list[str]:
    config = load_config(str(config_path))
    projects: list[str] = []
    for name, project in config.projects.items():
        source_path = project.source.path or ""
        if "bird_dev" in source_path and "dev_databases" in source_path:
            projects.append(name)
    return sorted(projects)


def _selected_projects(args: argparse.Namespace) -> list[str]:
    explicit = _split_csv(args.neo4j_projects)
    if explicit:
        return explicit

    config_path = Path(args.config).expanduser()
    task_args = _task_args(args)
    if args.task == "benchmark":
        selected = _benchmark_db_filter(task_args)
        projects = selected or _configured_bird_dev_projects(config_path)
        if _benchmark_uses_bird_global(task_args):
            projects = ["bird", *projects]
    else:
        selected = _extract_db_filter(task_args)
        projects = selected or _configured_bird_dev_projects(config_path)

    deduped: list[str] = []
    for project in projects:
        if project not in deduped:
            deduped.append(project)
    if not deduped:
        raise ValueError("No Neo4j projects selected. Pass --neo4j-projects explicitly.")
    return deduped


def _common_instance_args(args: argparse.Namespace) -> list[str]:
    return [
        "--config",
        str(Path(args.config).expanduser()),
        "--env-file",
        str(Path(args.env_file).expanduser()),
        "--base-dir",
        str(Path(args.base_dir).expanduser()),
        "--heap-initial",
        args.heap_initial,
        "--heap-max",
        args.heap_max,
        "--pagecache",
        args.pagecache,
    ]


def _python(args: argparse.Namespace) -> list[str]:
    return shlex.split(args.python_command)


def _instance_status_command(args: argparse.Namespace, projects: list[str]) -> str:
    return _quote(
        [
            *_python(args),
            "-m",
            "scripts.neo4j_instances",
            "status",
            *projects,
            *_common_instance_args(args),
        ]
    )


def _job_command(args: argparse.Namespace) -> str:
    return _quote(
        [
            *_python(args),
            "-m",
            _task_config(args)["module"],
            *_task_args(args),
        ]
    )


def _node_file(args: argparse.Namespace) -> Path:
    name = _safe_name(args.neo4j_job_name, DEFAULT_NEO4J_JOB_NAME)
    return Path(args.neo4j_slurm_dir).expanduser() / f"{name}.node"


def _job_id_file(args: argparse.Namespace) -> Path:
    default = _task_config(args)["default_job_name"]
    return Path(args.slurm_dir).expanduser() / f"{_safe_name(args.job_name, default)}.jobid"


def _read_saved_neo4j_job(args: argparse.Namespace) -> tuple[str, str]:
    if args.node:
        return (args.neo4j_job_id or "", args.node)

    path = _node_file(args)
    if not path.exists():
        raise ValueError(f"No Neo4j node file found: {path}")
    parts = path.read_text(encoding="utf-8").strip().split()
    if len(parts) < 2:
        raise ValueError(f"Invalid Neo4j node file: {path}")
    job_id, node = parts[0], parts[1]
    if args.neo4j_job_id and args.neo4j_job_id != job_id:
        raise ValueError(f"Neo4j node file has job {job_id}, not {args.neo4j_job_id}")
    return job_id, node


def _squeue_state(job_id: str) -> str:
    if not job_id:
        return ""
    result = subprocess.run(
        ["squeue", "-h", "-j", job_id, "-o", "%T"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _squeue_node(job_id: str) -> str:
    if not job_id:
        return ""
    result = subprocess.run(
        ["squeue", "-h", "-j", job_id, "-o", "%N"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _neo4j_job_is_running(job_id: str) -> bool:
    return _squeue_state(job_id) in {"RUNNING", "COMPLETING"}


def _run_in_neo4j_job(args: argparse.Namespace, job_id: str, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "srun",
            f"--jobid={job_id}",
            "--overlap",
            "bash",
            "-lc",
            f"cd {shlex.quote(str(ROOT))} && {command}",
        ],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _neo4j_projects_ready(args: argparse.Namespace, job_id: str, projects: list[str]) -> bool:
    result = _run_in_neo4j_job(args, job_id, _instance_status_command(args, projects))
    return result.returncode == 0


def _instance_start_command(args: argparse.Namespace, projects: list[str]) -> str:
    return _quote(
        [
            *_python(args),
            "-m",
            "scripts.neo4j_instances",
            "start",
            *projects,
            *_common_instance_args(args),
            "--start-grace",
            str(args.neo4j_start_grace),
        ]
    )


def _start_projects_in_existing_neo4j_job(args: argparse.Namespace, job_id: str, projects: list[str]) -> None:
    result = _run_in_neo4j_job(args, job_id, _instance_start_command(args, projects))
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Failed to start Neo4j projects in job {job_id}: {message}")


def _submit_neo4j_job(args: argparse.Namespace, projects: list[str]) -> str:
    cmd = [
        *_python(args),
        "-m",
        "scripts.neo4j_slurm",
        "submit",
        *projects,
        "--job-name",
        args.neo4j_job_name,
        "--slurm-dir",
        str(Path(args.neo4j_slurm_dir).expanduser()),
        "--partition",
        args.neo4j_partition,
        "--cpus-per-task",
        str(args.neo4j_cpus_per_task),
        "--mem",
        args.neo4j_mem,
        "--time",
        args.neo4j_time,
        "--config",
        str(Path(args.config).expanduser()),
        "--env-file",
        str(Path(args.env_file).expanduser()),
        "--base-dir",
        str(Path(args.base_dir).expanduser()),
        "--heap-initial",
        args.heap_initial,
        "--heap-max",
        args.heap_max,
        "--pagecache",
        args.pagecache,
        "--start-grace",
        str(args.neo4j_start_grace),
        "--stop-timeout",
        str(args.neo4j_stop_timeout),
        "--check-interval",
        str(args.neo4j_check_interval),
        "--python-command",
        args.python_command,
    ]
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    match = re.search(r"submitted\s+(\S+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not parse Neo4j job id from output: {result.stdout.strip()}")
    return match.group(1)


def _wait_for_neo4j_ready(args: argparse.Namespace, job_id: str, projects: list[str]) -> tuple[str, str]:
    deadline = time.time() + args.neo4j_ready_timeout
    last = ""
    while time.time() < deadline:
        state = _squeue_state(job_id)
        node = _squeue_node(job_id)
        last = f"state={state!r} node={node!r}"
        if state == "RUNNING" and node and node not in {"(None)", "None", ""}:
            try:
                saved_job, saved_node = _read_saved_neo4j_job(args)
                if saved_job == job_id and saved_node:
                    node = saved_node
            except Exception:
                pass
            if _neo4j_projects_ready(args, job_id, projects):
                return job_id, node
        if state in {"FAILED", "CANCELLED", "COMPLETED", "TIMEOUT", "NODE_FAIL"}:
            raise RuntimeError(f"Neo4j job {job_id} ended before ready: {state}")
        time.sleep(args.neo4j_ready_poll)
    raise TimeoutError(f"Neo4j job {job_id} was not ready after {args.neo4j_ready_timeout}s ({last})")


def _ensure_neo4j_ready(args: argparse.Namespace, projects: list[str]) -> tuple[str, str]:
    if args.node:
        return args.neo4j_job_id or "", args.node

    try:
        job_id, node = _read_saved_neo4j_job(args)
    except Exception:
        job_id, node = "", ""

    if job_id and _neo4j_job_is_running(job_id):
        if not _neo4j_projects_ready(args, job_id, projects):
            _start_projects_in_existing_neo4j_job(args, job_id, projects)
            job_id, node = _wait_for_neo4j_ready(args, job_id, projects)
        return job_id, node or _squeue_node(job_id)

    if args.dry_run:
        raise ValueError("Neo4j job is not running; dry-run will not auto-start it")
    if args.no_auto_start_neo4j:
        raise ValueError("Neo4j job is not running and --no-auto-start-neo4j was set")

    job_id = _submit_neo4j_job(args, projects)
    return _wait_for_neo4j_ready(args, job_id, projects)


def _write_batch_script(args: argparse.Namespace, projects: list[str], neo4j_job: str, node: str) -> Path:
    config = _task_config(args)
    slurm_dir = Path(args.slurm_dir).expanduser()
    slurm_dir.mkdir(parents=True, exist_ok=True)

    job_name = _safe_name(args.job_name, config["default_job_name"])
    script_path = slurm_dir / f"{job_name}.sbatch"
    node_file = slurm_dir / f"{job_name}.node"
    projects_file = slurm_dir / f"{job_name}.projects.json"
    projects_file.write_text(json.dumps(projects, indent=2) + "\n", encoding="utf-8")

    status_cmd = _instance_status_command(args, projects)
    job_cmd = _job_command(args)
    label = config["label"]

    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"#SBATCH --job-name={job_name}",
                f"#SBATCH --partition={args.partition}",
                f"#SBATCH --nodelist={node}",
                f"#SBATCH --cpus-per-task={args.cpus_per_task}",
                f"#SBATCH --mem={args.mem}",
                f"#SBATCH --time={args.time}",
                f"#SBATCH --output={slurm_dir}/{job_name}-%j.out",
                f"#SBATCH --error={slurm_dir}/{job_name}-%j.err",
                "",
                "set -euo pipefail",
                f"cd {shlex.quote(str(ROOT))}",
                f"mkdir -p {shlex.quote(str(slurm_dir))}",
                f"echo \"${{SLURM_JOB_ID}} $(hostname)\" > {shlex.quote(str(node_file))}",
                f"echo 'Neo4j job: {neo4j_job or '(manual node)'} on {node}'",
                f"echo 'Neo4j projects: {', '.join(projects)}'",
                "",
                "echo \"Checking Pontis Neo4j status at $(date)\"",
                f"{status_cmd}",
                "",
                f"echo \"Running {label} at $(date)\"",
                "set +e",
                f"{job_cmd}",
                "job_status=$?",
                "set -e",
                f"echo \"{label} exited with ${{job_status}} at $(date)\"",
                "exit ${job_status}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def cmd_submit(args: argparse.Namespace) -> int:
    projects = _selected_projects(args)
    neo4j_job, node = _ensure_neo4j_ready(args, projects)
    script_path = _write_batch_script(args, projects, neo4j_job, node)
    if args.dry_run:
        print(f"dry-run script\t{script_path}")
        print(f"node: {node}")
        print(f"neo4j projects: {', '.join(projects)}")
        return 0

    result = subprocess.run(
        ["sbatch", "--parsable", str(script_path)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode

    job_id = result.stdout.strip().split(";", 1)[0]
    _job_id_file(args).write_text(job_id + "\n", encoding="utf-8")
    print(f"submitted {job_id}\t{script_path}")
    print(f"node: {node}")
    print(f"neo4j projects: {', '.join(projects)}")
    return 0


def _read_job_id(args: argparse.Namespace) -> str:
    if args.job_id:
        return args.job_id
    path = _job_id_file(args)
    if not path.exists():
        raise ValueError(f"No job id file found: {path}")
    return path.read_text(encoding="utf-8").strip()


def cmd_status(args: argparse.Namespace) -> int:
    job_id = _read_job_id(args)
    result = subprocess.run(
        ["squeue", "-j", job_id, "-o", "%.18i %.9P %.30j %.8u %.2t %.10M %.6D %R"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)

    slurm_dir = Path(args.slurm_dir).expanduser()
    job_name = _safe_name(args.job_name, _task_config(args)["default_job_name"])
    for suffix in ("node", "projects.json"):
        path = slurm_dir / f"{job_name}.{suffix}"
        if path.exists():
            print(f"{suffix}\t{path.read_text(encoding='utf-8').strip()}")
    return result.returncode


def cmd_cancel(args: argparse.Namespace) -> int:
    job_id = _read_job_id(args)
    return subprocess.run(["scancel", job_id], text=True, check=False).returncode


def _add_job_args(subparser: argparse.ArgumentParser, default_job_name: str):
    subparser.add_argument("--job-name", default=default_job_name)
    subparser.add_argument("--slurm-dir", default=str(DEFAULT_SLURM_DIR))


def _add_submit_args(subparser: argparse.ArgumentParser, task: str):
    config = TASKS[task]
    _add_job_args(subparser, config["default_job_name"])
    subparser.add_argument("--partition", "-p", default="small")
    subparser.add_argument("--cpus-per-task", type=int, default=config["cpus"])
    subparser.add_argument("--mem", default=config["mem"])
    subparser.add_argument("--time", default=config["time"])
    subparser.add_argument("--config", default=str(ROOT / "pontis.yml"))
    subparser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    subparser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    subparser.add_argument("--heap-initial", default="128m")
    subparser.add_argument("--heap-max", default="256m")
    subparser.add_argument("--pagecache", default="64m")
    subparser.add_argument("--python-command", default="uv run python")
    subparser.add_argument("--neo4j-job-name", default=DEFAULT_NEO4J_JOB_NAME)
    subparser.add_argument("--neo4j-slurm-dir", default=str(DEFAULT_SLURM_DIR))
    subparser.add_argument("--neo4j-job-id")
    subparser.add_argument("--node", help="Override Neo4j node, e.g. GPU39")
    subparser.add_argument("--no-auto-start-neo4j", action="store_true")
    subparser.add_argument("--neo4j-partition", default="small")
    subparser.add_argument("--neo4j-cpus-per-task", type=int, default=4)
    subparser.add_argument("--neo4j-mem", default="24G")
    subparser.add_argument("--neo4j-time", default="10-00:00:00")
    subparser.add_argument("--neo4j-start-grace", type=float, default=4.0)
    subparser.add_argument("--neo4j-stop-timeout", type=float, default=20.0)
    subparser.add_argument("--neo4j-check-interval", type=int, default=60)
    subparser.add_argument("--neo4j-ready-timeout", type=float, default=300.0)
    subparser.add_argument("--neo4j-ready-poll", type=float, default=10.0)
    subparser.add_argument("--dry-run", action="store_true", help="Write the sbatch script but do not submit it")
    subparser.add_argument(
        "--neo4j-projects",
        help="Comma-separated Pontis projects to check. Default: selected BIRD dev projects.",
    )
    subparser.add_argument(
        config["args_name"],
        nargs=argparse.REMAINDER,
        help=f"Arguments passed to {config['module']} after --",
    )
    subparser.set_defaults(func=cmd_submit, task=task)


def _add_status_cancel_args(subparser: argparse.ArgumentParser, task: str, func):
    _add_job_args(subparser, TASKS[task]["default_job_name"])
    subparser.add_argument("--job-id")
    subparser.set_defaults(func=func, task=task)


def _add_task_parser(subparsers: argparse._SubParsersAction, task: str):
    task_parser = subparsers.add_parser(task, help=f"Submit/status/cancel BIRD {task} jobs")
    task_subparsers = task_parser.add_subparsers(dest="command", required=True)

    submit = task_subparsers.add_parser("submit", help=f"Submit {TASKS[task]['module']} on the Neo4j node")
    _add_submit_args(submit, task)

    status = task_subparsers.add_parser("status", help=f"Show saved BIRD {task} Slurm job status")
    _add_status_cancel_args(status, task, cmd_status)

    cancel = task_subparsers.add_parser("cancel", help=f"Cancel saved BIRD {task} Slurm job")
    _add_status_cancel_args(cancel, task, cmd_cancel)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="task", required=True)
    for task in TASKS:
        _add_task_parser(subparsers, task)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
