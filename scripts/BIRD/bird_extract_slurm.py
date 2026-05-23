"""Submit BIRD extract on the node that hosts the persistent Neo4j job.

Pontis local Neo4j instances listen on localhost ports, so extract must run on
the same Slurm node as the Neo4j job. This wrapper mirrors
scripts.BIRD.bird_benchmark_slurm: it reuses the saved Neo4j job when possible,
auto-starts it when needed, then submits a bash sbatch job pinned to that node.

Extract arguments are passed after ``--``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.BIRD.bird_benchmark_slurm import (
    DEFAULT_NEO4J_JOB_NAME,
    DEFAULT_SLURM_DIR,
    ROOT,
    _common_instance_args,
    _configured_bird_dev_projects,
    _ensure_neo4j_ready,
    _instance_status_command,
    _job_id_file,
    _python,
    _quote,
    _safe_name,
    _split_csv,
)
from scripts.neo4j_instances import DEFAULT_BASE_DIR, DEFAULT_ENV_FILE


DEFAULT_JOB_NAME = "bird-extract"


def _extract_db_filter(extract_args: list[str]) -> list[str]:
    """Return positional BIRD db filters from scripts.BIRD.extract args."""
    dbs: list[str] = []
    index = 0
    skip_value_for = {"--run-id", "--workers", "--column-workers"}
    while index < len(extract_args):
        arg = extract_args[index]
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


def _selected_projects(args: argparse.Namespace) -> list[str]:
    explicit = _split_csv(args.neo4j_projects)
    if explicit:
        return explicit

    config_path = Path(args.config).expanduser()
    selected = _extract_db_filter(args.extract_args)
    projects = selected or _configured_bird_dev_projects(config_path)

    deduped: list[str] = []
    for project in projects:
        if project not in deduped:
            deduped.append(project)
    if not deduped:
        raise ValueError("No Neo4j projects selected. Pass a db name or --neo4j-projects.")
    return deduped


def _extract_command(args: argparse.Namespace) -> str:
    return _quote(
        [
            *_python(args),
            "-m",
            "scripts.BIRD.extract",
            *args.extract_args,
        ]
    )


def _write_batch_script(args: argparse.Namespace, projects: list[str], neo4j_job: str, node: str) -> Path:
    slurm_dir = Path(args.slurm_dir).expanduser()
    slurm_dir.mkdir(parents=True, exist_ok=True)

    job_name = _safe_name(args.job_name)
    script_path = slurm_dir / f"{job_name}.sbatch"
    node_file = slurm_dir / f"{job_name}.node"
    projects_file = slurm_dir / f"{job_name}.projects.json"
    projects_file.write_text(json.dumps(projects, indent=2) + "\n", encoding="utf-8")

    status_cmd = _instance_status_command(args, projects)
    extract_cmd = _extract_command(args)

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
                f"cd {str(ROOT)!r}",
                f"mkdir -p {str(slurm_dir)!r}",
                f"echo \"${{SLURM_JOB_ID}} $(hostname)\" > {str(node_file)!r}",
                f"echo 'Neo4j job: {neo4j_job or '(manual node)'} on {node}'",
                f"echo 'Neo4j projects: {', '.join(projects)}'",
                "",
                "echo \"Checking Pontis Neo4j status at $(date)\"",
                f"{status_cmd}",
                "",
                "echo \"Running BIRD extract at $(date)\"",
                "set +e",
                f"{extract_cmd}",
                "extract_status=$?",
                "set -e",
                "echo \"BIRD extract exited with ${extract_status} at $(date)\"",
                "exit ${extract_status}",
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
    job_name = _safe_name(args.job_name)
    for suffix in ("node", "projects.json"):
        path = slurm_dir / f"{job_name}.{suffix}"
        if path.exists():
            print(f"{suffix}\t{path.read_text(encoding='utf-8').strip()}")
    return result.returncode


def cmd_cancel(args: argparse.Namespace) -> int:
    job_id = _read_job_id(args)
    return subprocess.run(["scancel", job_id], text=True, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_job_args(subparser: argparse.ArgumentParser):
        subparser.add_argument("--job-name", default=DEFAULT_JOB_NAME)
        subparser.add_argument("--slurm-dir", default=str(DEFAULT_SLURM_DIR))

    submit = subparsers.add_parser(
        "submit",
        help="Submit scripts.BIRD.extract on the persistent Neo4j node",
    )
    add_job_args(submit)
    submit.add_argument("--partition", "-p", default="small")
    submit.add_argument("--cpus-per-task", type=int, default=48)
    submit.add_argument("--mem", default="192G")
    submit.add_argument("--time", default="24:00:00")
    submit.add_argument("--config", default=str(ROOT / "pontis.yml"))
    submit.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    submit.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    submit.add_argument("--heap-initial", default="128m")
    submit.add_argument("--heap-max", default="256m")
    submit.add_argument("--pagecache", default="64m")
    submit.add_argument("--python-command", default="uv run python")
    submit.add_argument("--neo4j-job-name", default=DEFAULT_NEO4J_JOB_NAME)
    submit.add_argument("--neo4j-slurm-dir", default=str(DEFAULT_SLURM_DIR))
    submit.add_argument("--neo4j-job-id")
    submit.add_argument("--node", help="Override Neo4j node, e.g. GPU39")
    submit.add_argument("--no-auto-start-neo4j", action="store_true")
    submit.add_argument("--neo4j-partition", default="small")
    submit.add_argument("--neo4j-cpus-per-task", type=int, default=4)
    submit.add_argument("--neo4j-mem", default="24G")
    submit.add_argument("--neo4j-time", default="10-00:00:00")
    submit.add_argument("--neo4j-start-grace", type=float, default=4.0)
    submit.add_argument("--neo4j-stop-timeout", type=float, default=20.0)
    submit.add_argument("--neo4j-check-interval", type=int, default=60)
    submit.add_argument("--neo4j-ready-timeout", type=float, default=300.0)
    submit.add_argument("--neo4j-ready-poll", type=float, default=10.0)
    submit.add_argument("--dry-run", action="store_true", help="Write the sbatch script but do not submit it")
    submit.add_argument(
        "--neo4j-projects",
        help="Comma-separated Pontis projects to check. Default: extract db arg or all configured BIRD dev projects.",
    )
    submit.add_argument(
        "extract_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to scripts.BIRD.extract after --",
    )
    submit.set_defaults(func=cmd_submit)

    status = subparsers.add_parser("status", help="Show saved extract Slurm job status")
    add_job_args(status)
    status.add_argument("--job-id")
    status.set_defaults(func=cmd_status)

    cancel = subparsers.add_parser("cancel", help="Cancel saved extract Slurm job")
    add_job_args(cancel)
    cancel.add_argument("--job-id")
    cancel.set_defaults(func=cmd_cancel)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "extract_args") and args.extract_args and args.extract_args[0] == "--":
        args.extract_args = args.extract_args[1:]
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
