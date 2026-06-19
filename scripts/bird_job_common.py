"""Shared Slurm helpers for BIRD extract and benchmark submitters.

The public entrypoints are intentionally split by workload:

- ``scripts.bird_extract_slurm`` submits preprocessing/extract jobs.
- ``scripts.bird_benchmark_slurm`` submits benchmark evaluation jobs.

This module only contains mechanics common to both: Neo4j co-location, Slurm
script generation, and model backend environment setup.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from scripts.neo4j_instances import DEFAULT_BASE_DIR, DEFAULT_ENV_FILE
from storage.config import load_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SLURM_DIR = DEFAULT_BASE_DIR.parent / "slurm"
DEFAULT_NEO4J_JOB_NAME = "pontis-neo4j"


@dataclass(frozen=True)
class BirdJobSpec:
    module: str
    label: str
    default_job_name: str
    default_cpus: int
    default_mem: str
    default_time: str
    remainder_name: str
    default_projects_from_args: callable


def safe_name(name: str, default: str = "bird-job") -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or default


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def task_args(args: argparse.Namespace, spec: BirdJobSpec) -> list[str]:
    values = list(getattr(args, spec.remainder_name, []))
    if values and values[0] == "--":
        values = values[1:]
    return values


def quote(parts: list[str | Path]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def python_cmd(args: argparse.Namespace) -> list[str]:
    return shlex.split(args.python_command)


def configured_bird_dev_projects(config_path: Path) -> list[str]:
    config = load_config(str(config_path))
    projects: list[str] = []
    for name, project in config.projects.items():
        source_path = project.source.path or ""
        if "bird_dev" in source_path and "dev_databases" in source_path:
            projects.append(name)
    return sorted(projects)


def selected_projects(args: argparse.Namespace, spec: BirdJobSpec) -> list[str]:
    explicit = split_csv(args.neo4j_projects)
    if explicit:
        return explicit

    config_path = Path(args.config).expanduser()
    selected = spec.default_projects_from_args(task_args(args, spec))
    projects = selected or configured_bird_dev_projects(config_path)

    deduped: list[str] = []
    for project in projects:
        if project not in deduped:
            deduped.append(project)
    if not deduped:
        raise ValueError("No Neo4j projects selected. Pass --neo4j-projects explicitly.")
    return deduped


def common_instance_args(args: argparse.Namespace) -> list[str]:
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


def instance_status_command(args: argparse.Namespace, projects: list[str]) -> str:
    return quote(
        [
            *python_cmd(args),
            "-m",
            "scripts.neo4j_instances",
            "status",
            *projects,
            *common_instance_args(args),
        ]
    )


def instance_start_command(args: argparse.Namespace, projects: list[str]) -> str:
    return quote(
        [
            *python_cmd(args),
            "-m",
            "scripts.neo4j_instances",
            "start",
            *projects,
            *common_instance_args(args),
            "--start-grace",
            str(args.neo4j_start_grace),
        ]
    )


def job_command(args: argparse.Namespace, spec: BirdJobSpec) -> str:
    return quote([*python_cmd(args), "-m", spec.module, *task_args(args, spec)])


def node_file(args: argparse.Namespace) -> Path:
    name = safe_name(args.neo4j_job_name, DEFAULT_NEO4J_JOB_NAME)
    return Path(args.neo4j_slurm_dir).expanduser() / f"{name}.node"


def job_id_file(args: argparse.Namespace, spec: BirdJobSpec) -> Path:
    return Path(args.slurm_dir).expanduser() / f"{safe_name(args.job_name, spec.default_job_name)}.jobid"


def _remote_key_lines(args: argparse.Namespace) -> list[str]:
    if args.remote_api_key:
        key = shlex.quote(args.remote_api_key)
        return [
            f"export MODEL_API_KEY={key}",
            f"export OPENAI_API_KEY={key}",
            f"export PONTIS_AGENT_API_KEY={key}",
            f"export PONTIS_EXTRACTOR_API_KEY={key}",
        ]

    key_env = shlex.quote(args.remote_api_key_env)
    return [
        f"REMOTE_API_KEY_ENV={key_env}",
        "if [[ -z \"${!REMOTE_API_KEY_ENV:-}\" ]]; then",
        "  echo \"Remote API key env var is empty: ${REMOTE_API_KEY_ENV}\" >&2",
        "  exit 1",
        "fi",
        "export MODEL_API_KEY=\"${!REMOTE_API_KEY_ENV}\"",
        "export OPENAI_API_KEY=\"${!REMOTE_API_KEY_ENV}\"",
        "export PONTIS_AGENT_API_KEY=\"${!REMOTE_API_KEY_ENV}\"",
        "export PONTIS_EXTRACTOR_API_KEY=\"${!REMOTE_API_KEY_ENV}\"",
    ]


def model_env_lines(args: argparse.Namespace) -> list[str]:
    backend = args.model_backend
    lines = ["echo \"Model backend: %s\"" % backend]

    if backend == "env":
        if args.disable_thinking:
            lines.extend(thinking_off_lines())
        return lines + [""]

    if backend == "local":
        if args.local_model_endpoint and args.local_model_endpoint_file:
            raise ValueError("Use only one of --local-model-endpoint or --local-model-endpoint-file")
        if not args.local_model_endpoint and not args.local_model_endpoint_file:
            raise ValueError("--model-backend local requires --local-model-endpoint or --local-model-endpoint-file")

        if args.local_model_endpoint_file:
            endpoint_file = shlex.quote(str(Path(args.local_model_endpoint_file).expanduser()))
            lines.extend(
                [
                    f"LOCAL_MODEL_ENDPOINT_FILE={endpoint_file}",
                    "if [[ ! -s \"${LOCAL_MODEL_ENDPOINT_FILE}\" ]]; then",
                    "  echo \"Local model endpoint file is missing or empty: ${LOCAL_MODEL_ENDPOINT_FILE}\" >&2",
                    "  exit 1",
                    "fi",
                    "read -r _local_model_job _local_model_host _local_model_port _local_model_extra < \"${LOCAL_MODEL_ENDPOINT_FILE}\"",
                    "if [[ \"${_local_model_job}\" == http://* || \"${_local_model_job}\" == https://* ]]; then",
                    "  LOCAL_MODEL_BASE_URL=\"${_local_model_job}\"",
                    "elif [[ -n \"${_local_model_host:-}\" && -n \"${_local_model_port:-}\" ]]; then",
                    "  LOCAL_MODEL_BASE_URL=\"http://${_local_model_host}:${_local_model_port}/v1\"",
                    "else",
                    "  echo \"Invalid local model endpoint file: ${LOCAL_MODEL_ENDPOINT_FILE}\" >&2",
                    "  exit 1",
                    "fi",
                ]
            )
        else:
            lines.append(f"LOCAL_MODEL_BASE_URL={shlex.quote(args.local_model_endpoint)}")

        model_name = shlex.quote(args.local_model_name)
        api_key = shlex.quote(args.local_model_api_key)
        lines.extend(
            [
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY",
                "if [[ -n \"${_local_model_host:-}\" ]]; then",
                "  export NO_PROXY=\"${NO_PROXY:-},${_local_model_host}\"",
                "else",
                "  export NO_PROXY=\"${NO_PROXY:-},localhost,127.0.0.1\"",
                "fi",
                "export MODEL_API_URL=\"${LOCAL_MODEL_BASE_URL}\"",
                "export OPENAI_BASE_URL=\"${LOCAL_MODEL_BASE_URL}\"",
                f"export MODEL_API_KEY={api_key}",
                f"export OPENAI_API_KEY={api_key}",
                f"export PONTIS_AGENT_API_KEY={api_key}",
                f"export PONTIS_EXTRACTOR_API_KEY={api_key}",
                f"export MODEL_NAME={model_name}",
                f"export OPENAI_MODEL={model_name}",
                f"export PONTIS_AGENT_MODEL={model_name}",
                f"export PONTIS_EXTRACTOR_MODEL={model_name}",
                "export PONTIS_AGENT_THINKING=false",
                "export PONTIS_EXTRACTOR_THINKING=false",
                "echo \"Local model endpoint: ${LOCAL_MODEL_BASE_URL}\"",
                "echo \"Local model name: ${MODEL_NAME}\"",
                "",
            ]
        )
        return lines

    if backend == "remote":
        if not args.remote_base_url:
            raise ValueError("--model-backend remote requires --remote-base-url")
        if not args.remote_model:
            raise ValueError("--model-backend remote requires --remote-model")

        base_url = shlex.quote(args.remote_base_url)
        model_name = shlex.quote(args.remote_model)
        lines.extend(
            [
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY",
                f"export MODEL_API_URL={base_url}",
                f"export OPENAI_BASE_URL={base_url}",
                f"export MODEL_NAME={model_name}",
                f"export OPENAI_MODEL={model_name}",
                f"export PONTIS_AGENT_MODEL={model_name}",
                f"export PONTIS_EXTRACTOR_MODEL={model_name}",
                *_remote_key_lines(args),
            ]
        )
        if args.disable_thinking:
            lines.extend(thinking_off_lines())
        lines.extend(
            [
                "echo \"Remote model endpoint: ${MODEL_API_URL}\"",
                "echo \"Remote model name: ${MODEL_NAME}\"",
                "",
            ]
        )
        return lines

    raise ValueError(f"Unknown model backend: {backend}")


def thinking_off_lines() -> list[str]:
    return [
        "export PONTIS_AGENT_THINKING=false",
        "export PONTIS_EXTRACTOR_THINKING=false",
    ]


def read_saved_neo4j_job(args: argparse.Namespace) -> tuple[str, str]:
    if args.node:
        return (args.neo4j_job_id or "", args.node)

    path = node_file(args)
    if not path.exists():
        raise ValueError(f"No Neo4j node file found: {path}")
    parts = path.read_text(encoding="utf-8").strip().split()
    if len(parts) < 2:
        raise ValueError(f"Invalid Neo4j node file: {path}")
    job_id, node = parts[0], parts[1]
    if args.neo4j_job_id and args.neo4j_job_id != job_id:
        raise ValueError(f"Neo4j node file has job {job_id}, not {args.neo4j_job_id}")
    return job_id, node


def squeue_state(job_id: str) -> str:
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


def squeue_node(job_id: str) -> str:
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


def neo4j_job_is_running(job_id: str) -> bool:
    return squeue_state(job_id) in {"RUNNING", "COMPLETING"}


def run_in_neo4j_job(args: argparse.Namespace, job_id: str, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["srun", f"--jobid={job_id}", "--overlap", "bash", "-lc", f"cd {shlex.quote(str(ROOT))} && {command}"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def neo4j_projects_ready(args: argparse.Namespace, job_id: str, projects: list[str]) -> bool:
    result = run_in_neo4j_job(args, job_id, instance_status_command(args, projects))
    return result.returncode == 0


def start_projects_in_existing_neo4j_job(args: argparse.Namespace, job_id: str, projects: list[str]) -> None:
    result = run_in_neo4j_job(args, job_id, instance_start_command(args, projects))
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Failed to start Neo4j projects in job {job_id}: {message}")


def submit_neo4j_job(args: argparse.Namespace, projects: list[str]) -> str:
    cmd = [
        *python_cmd(args),
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
        *common_instance_args(args),
        "--start-grace",
        str(args.neo4j_start_grace),
        "--stop-timeout",
        str(args.neo4j_stop_timeout),
        "--check-interval",
        str(args.neo4j_check_interval),
        "--python-command",
        args.python_command,
    ]
    result = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    match = re.search(r"submitted\s+(\S+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not parse Neo4j job id from output: {result.stdout.strip()}")
    return match.group(1)


def wait_for_neo4j_ready(args: argparse.Namespace, job_id: str, projects: list[str]) -> tuple[str, str]:
    deadline = time.time() + args.neo4j_ready_timeout
    last = ""
    while time.time() < deadline:
        state = squeue_state(job_id)
        node = squeue_node(job_id)
        last = f"state={state!r} node={node!r}"
        if state == "RUNNING" and node and node not in {"(None)", "None", ""}:
            try:
                saved_job, saved_node = read_saved_neo4j_job(args)
                if saved_job == job_id and saved_node:
                    node = saved_node
            except Exception:
                pass
            if neo4j_projects_ready(args, job_id, projects):
                return job_id, node
        if state in {"FAILED", "CANCELLED", "COMPLETED", "TIMEOUT", "NODE_FAIL"}:
            raise RuntimeError(f"Neo4j job {job_id} ended before ready: {state}")
        time.sleep(args.neo4j_ready_poll)
    raise TimeoutError(f"Neo4j job {job_id} was not ready after {args.neo4j_ready_timeout}s ({last})")


def ensure_neo4j_ready(args: argparse.Namespace, projects: list[str]) -> tuple[str, str]:
    if args.node:
        return args.neo4j_job_id or "", args.node

    try:
        job_id, node = read_saved_neo4j_job(args)
    except Exception:
        job_id, node = "", ""

    if job_id and neo4j_job_is_running(job_id):
        if not neo4j_projects_ready(args, job_id, projects):
            start_projects_in_existing_neo4j_job(args, job_id, projects)
            job_id, node = wait_for_neo4j_ready(args, job_id, projects)
        return job_id, node or squeue_node(job_id)

    if args.dry_run:
        raise ValueError("Neo4j job is not running; dry-run will not auto-start it")
    if args.no_auto_start_neo4j:
        raise ValueError("Neo4j job is not running and --no-auto-start-neo4j was set")

    job_id = submit_neo4j_job(args, projects)
    return wait_for_neo4j_ready(args, job_id, projects)


def write_batch_script(args: argparse.Namespace, spec: BirdJobSpec, projects: list[str], neo4j_job: str, node: str) -> Path:
    slurm_dir = Path(args.slurm_dir).expanduser()
    slurm_dir.mkdir(parents=True, exist_ok=True)

    job_name = safe_name(args.job_name, spec.default_job_name)
    script_path = slurm_dir / f"{job_name}.sbatch"
    node_path = slurm_dir / f"{job_name}.node"
    projects_path = slurm_dir / f"{job_name}.projects.json"
    projects_path.write_text(json.dumps(projects, indent=2) + "\n", encoding="utf-8")

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
                f"export PYTHONPATH={shlex.quote(str(ROOT.parent / 'tools'))}:{shlex.quote(str(ROOT.parent))}:${{PYTHONPATH:-}}",
                f"mkdir -p {shlex.quote(str(slurm_dir))}",
                f"echo \"${{SLURM_JOB_ID}} $(hostname)\" > {shlex.quote(str(node_path))}",
                f"echo 'Neo4j job: {neo4j_job or '(manual node)'} on {node}'",
                f"echo 'Neo4j projects: {', '.join(projects)}'",
                "",
                *model_env_lines(args),
                "echo \"Checking Pontis Neo4j status at $(date)\"",
                instance_status_command(args, projects),
                "",
                f"echo \"Running {spec.label} at $(date)\"",
                "set +e",
                job_command(args, spec),
                "job_status=$?",
                "set -e",
                f"echo \"{spec.label} exited with ${{job_status}} at $(date)\"",
                "exit ${job_status}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def submit(args: argparse.Namespace, spec: BirdJobSpec) -> int:
    projects = selected_projects(args, spec)
    neo4j_job, node = ensure_neo4j_ready(args, projects)
    script_path = write_batch_script(args, spec, projects, neo4j_job, node)
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
        print(result.stderr.strip())
        return result.returncode

    job_id = result.stdout.strip().split(";", 1)[0]
    job_id_file(args, spec).write_text(job_id + "\n", encoding="utf-8")
    print(f"submitted {job_id}\t{script_path}")
    print(f"node: {node}")
    print(f"neo4j projects: {', '.join(projects)}")
    return 0


def read_job_id(args: argparse.Namespace, spec: BirdJobSpec) -> str:
    if args.job_id:
        return args.job_id
    path = job_id_file(args, spec)
    if not path.exists():
        raise ValueError(f"No job id file found: {path}")
    return path.read_text(encoding="utf-8").strip()


def status(args: argparse.Namespace, spec: BirdJobSpec) -> int:
    job_id = read_job_id(args, spec)
    result = subprocess.run(
        ["squeue", "-j", job_id, "-o", "%.18i %.9P %.40j %.8u %.2t %.10M %.6D %R"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip())

    slurm_dir = Path(args.slurm_dir).expanduser()
    job_name = safe_name(args.job_name, spec.default_job_name)
    for suffix in ("node", "projects.json"):
        path = slurm_dir / f"{job_name}.{suffix}"
        if path.exists():
            print(f"{suffix}\t{path.read_text(encoding='utf-8').strip()}")
    return result.returncode


def cancel(args: argparse.Namespace, spec: BirdJobSpec) -> int:
    return subprocess.run(["scancel", read_job_id(args, spec)], text=True, check=False).returncode


def add_common_submit_args(parser: argparse.ArgumentParser, spec: BirdJobSpec) -> None:
    parser.add_argument("--job-name", default=spec.default_job_name)
    parser.add_argument("--slurm-dir", default=str(DEFAULT_SLURM_DIR))
    parser.add_argument("--partition", "-p", default="small")
    parser.add_argument("--cpus-per-task", type=int, default=spec.default_cpus)
    parser.add_argument("--mem", default=spec.default_mem)
    parser.add_argument("--time", default=spec.default_time)
    parser.add_argument("--config", default=str(ROOT / "pontis.yml"))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    parser.add_argument("--heap-initial", default="128m")
    parser.add_argument("--heap-max", default="256m")
    parser.add_argument("--pagecache", default="64m")
    parser.add_argument("--python-command", default="uv run python")
    parser.add_argument("--neo4j-job-name", default=DEFAULT_NEO4J_JOB_NAME)
    parser.add_argument("--neo4j-slurm-dir", default=str(DEFAULT_SLURM_DIR))
    parser.add_argument("--neo4j-job-id")
    parser.add_argument("--node", help="Override Neo4j node, e.g. GPU39")
    parser.add_argument("--no-auto-start-neo4j", action="store_true")
    parser.add_argument("--neo4j-partition", default="small")
    parser.add_argument("--neo4j-cpus-per-task", type=int, default=4)
    parser.add_argument("--neo4j-mem", default="24G")
    parser.add_argument("--neo4j-time", default="10-00:00:00")
    parser.add_argument("--neo4j-start-grace", type=float, default=4.0)
    parser.add_argument("--neo4j-stop-timeout", type=float, default=20.0)
    parser.add_argument("--neo4j-check-interval", type=int, default=60)
    parser.add_argument("--neo4j-ready-timeout", type=float, default=300.0)
    parser.add_argument("--neo4j-ready-poll", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--neo4j-projects", help="Comma-separated Pontis projects to check")

    parser.add_argument("--model-backend", choices=["env", "local", "remote"], default="env")
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--local-model-endpoint", help="Local OpenAI-compatible endpoint, e.g. http://GPU247:18000/v1")
    parser.add_argument("--local-model-endpoint-file", help="Endpoint file containing URL or '<job_id> <host> <port>'")
    parser.add_argument("--local-model-name", default="Qwen/Qwen3-Coder-30B-A3B-Instruct")
    parser.add_argument("--local-model-api-key", default="local")
    parser.add_argument("--remote-base-url", help="Remote OpenAI-compatible endpoint")
    parser.add_argument("--remote-model", help="Remote model name")
    parser.add_argument("--remote-api-key", help="Remote API key value; prefer --remote-api-key-env")
    parser.add_argument("--remote-api-key-env", default="OPENAI_API_KEY")


def add_status_cancel_args(parser: argparse.ArgumentParser, spec: BirdJobSpec) -> None:
    parser.add_argument("--job-name", default=spec.default_job_name)
    parser.add_argument("--slurm-dir", default=str(DEFAULT_SLURM_DIR))
    parser.add_argument("--job-id")


def run_cli(argv: list[str] | None, spec: BirdJobSpec, description: str) -> int:
    parser = argparse.ArgumentParser(description=description)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit")
    add_common_submit_args(submit_parser, spec)
    submit_parser.add_argument(spec.remainder_name, nargs=argparse.REMAINDER, help=f"Arguments passed to {spec.module} after --")
    submit_parser.set_defaults(func=lambda args: submit(args, spec))

    status_parser = subparsers.add_parser("status")
    add_status_cancel_args(status_parser, spec)
    status_parser.set_defaults(func=lambda args: status(args, spec))

    cancel_parser = subparsers.add_parser("cancel")
    add_status_cancel_args(cancel_parser, spec)
    cancel_parser.set_defaults(func=lambda args: cancel(args, spec))

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
