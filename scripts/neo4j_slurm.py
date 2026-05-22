"""Submit Pontis Neo4j local instances as a long-running Slurm job.

The instance manager in scripts.neo4j_instances starts one local Neo4j process per
project. This wrapper keeps those processes inside a Slurm allocation: it
submits a batch job, starts the selected instances on the allocated node, then
keeps the job alive until it is cancelled or an instance stops.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

from scripts.neo4j_instances import DEFAULT_BASE_DIR, DEFAULT_ENV_FILE


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SLURM_DIR = DEFAULT_BASE_DIR.parent / "slurm"
DEFAULT_JOB_NAME = "pontis-neo4j"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or DEFAULT_JOB_NAME


def _quote_command(parts: list[str | Path]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _selected_projects(args: argparse.Namespace) -> list[str]:
    if args.all:
        return ["--all"]
    if not args.projects:
        raise ValueError("Specify project names or --all")
    return list(args.projects)


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


def _instance_command(args: argparse.Namespace, command: str, extra: list[str] | None = None) -> str:
    parts = shlex.split(args.python_command) + [
        "-m",
        "scripts.neo4j_instances",
        command,
        *_selected_projects(args),
        *_common_instance_args(args),
    ]
    if extra:
        parts.extend(extra)
    return _quote_command(parts)


def _write_batch_script(args: argparse.Namespace) -> Path:
    slurm_dir = Path(args.slurm_dir).expanduser()
    slurm_dir.mkdir(parents=True, exist_ok=True)

    job_name = _safe_name(args.job_name)
    script_path = slurm_dir / f"{job_name}.sbatch"
    node_file = slurm_dir / f"{job_name}.node"

    start_cmd = _instance_command(args, "start", ["--start-grace", str(args.start_grace)])
    status_cmd = _instance_command(args, "status")
    stop_cmd = _instance_command(args, "stop", ["--stop-timeout", str(args.stop_timeout)])

    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"#SBATCH --job-name={job_name}",
                f"#SBATCH --partition={args.partition}",
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
                "",
                "cleaned=0",
                "cleanup() {",
                "  if [ \"$cleaned\" -eq 0 ]; then",
                "    cleaned=1",
                "    echo \"Stopping Pontis Neo4j instances at $(date)\"",
                f"    {stop_cmd} || true",
                "  fi",
                "}",
                "trap 'cleanup; exit 0' TERM INT",
                "trap cleanup EXIT",
                "",
                "echo \"Starting Pontis Neo4j instances on $(hostname) at $(date)\"",
                f"{start_cmd}",
                "echo \"Pontis Neo4j instances started at $(date)\"",
                "",
                "while true; do",
                f"  sleep {int(args.check_interval)}",
                f"  if ! {status_cmd} >/dev/null 2>&1; then",
                "    echo \"One or more Neo4j instances stopped; ending Slurm job\" >&2",
                "    exit 1",
                "  fi",
                "done",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def _job_id_file(args: argparse.Namespace) -> Path:
    return Path(args.slurm_dir).expanduser() / f"{_safe_name(args.job_name)}.jobid"


def cmd_submit(args: argparse.Namespace) -> int:
    script_path = _write_batch_script(args)
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

    node_file = Path(args.slurm_dir).expanduser() / f"{_safe_name(args.job_name)}.node"
    if node_file.exists():
        print(f"node\t{node_file.read_text(encoding='utf-8').strip()}")
    return result.returncode


def cmd_cancel(args: argparse.Namespace) -> int:
    job_id = _read_job_id(args)
    result = subprocess.run(["scancel", job_id], text=True, check=False)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_shared(subparser: argparse.ArgumentParser, *, include_projects: bool):
        subparser.add_argument("--job-name", default=DEFAULT_JOB_NAME)
        subparser.add_argument("--slurm-dir", default=str(DEFAULT_SLURM_DIR))
        if include_projects:
            subparser.add_argument("projects", nargs="*", help="Project names from pontis.yml")
            subparser.add_argument("--all", action="store_true", help="Start all configured projects")

    submit = subparsers.add_parser("submit", help="Submit a Slurm job for selected Neo4j instances")
    add_shared(submit, include_projects=True)
    submit.add_argument("--partition", "-p", default="small")
    submit.add_argument("--cpus-per-task", type=int, default=4)
    submit.add_argument("--mem", default="24G")
    submit.add_argument("--time", default="10-00:00:00")
    submit.add_argument("--config", default=str(ROOT / "pontis.yml"))
    submit.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    submit.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    submit.add_argument("--heap-initial", default="128m")
    submit.add_argument("--heap-max", default="256m")
    submit.add_argument("--pagecache", default="64m")
    submit.add_argument("--start-grace", type=float, default=4.0)
    submit.add_argument("--stop-timeout", type=float, default=20.0)
    submit.add_argument("--check-interval", type=int, default=60)
    submit.add_argument("--python-command", default="uv run python")
    submit.set_defaults(func=cmd_submit)

    status = subparsers.add_parser("status", help="Show the saved Slurm job status")
    add_shared(status, include_projects=False)
    status.add_argument("--job-id")
    status.set_defaults(func=cmd_status)

    cancel = subparsers.add_parser("cancel", help="Cancel the saved Slurm job")
    add_shared(cancel, include_projects=False)
    cancel.add_argument("--job-id")
    cancel.set_defaults(func=cmd_cancel)

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
