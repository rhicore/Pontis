"""Submit BIRD benchmark jobs on the same node as Pontis Neo4j.

Examples:
    python -m scripts.bird_benchmark_slurm submit \
        --model-backend local \
        --local-model-endpoint-file ../workspace/baselines/pontis/model_slurm/qwen3coder30b-server-24k-a.endpoint \
        -- --run-id 20260615_california89 --db california_schools --workers 6 --db-workers 1

    python -m scripts.bird_benchmark_slurm submit \
        --model-backend remote \
        --remote-base-url https://api.deepseek.com \
        --remote-model deepseek-v4-flash \
        --remote-api-key-env DEEPSEEK_API_KEY \
        --disable-thinking \
        -- --run-id 20260615_api_eval --db california_schools --workers 6 --db-workers 1
"""

from __future__ import annotations

import sys

from scripts.bird_job_common import BirdJobSpec, run_cli, split_csv


def benchmark_projects_from_args(args: list[str]) -> list[str]:
    projects: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--db" and index + 1 < len(args):
            projects.extend(split_csv(args[index + 1]))
            index += 2
            continue
        if arg.startswith("--db="):
            projects.extend(split_csv(arg.split("=", 1)[1]))
        index += 1
    return projects


SPEC = BirdJobSpec(
    module="scripts.BIRD.run_bird_benchmark",
    label="BIRD benchmark",
    default_job_name="bird-benchmark",
    default_cpus=16,
    default_mem="64G",
    default_time="1-00:00:00",
    remainder_name="benchmark_args",
    default_projects_from_args=benchmark_projects_from_args,
)


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv, SPEC, __doc__ or "")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
