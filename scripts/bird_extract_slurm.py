"""Submit BIRD extract jobs on the same node as Pontis Neo4j.

Examples:
    python -m scripts.bird_extract_slurm submit \
        --model-backend remote \
        --remote-base-url https://api.deepseek.com \
        --remote-model deepseek-v4-flash \
        --remote-api-key-env DEEPSEEK_API_KEY \
        -- --run-id 20260615_extract --modules agent_disambiguate california_schools --workers 1

    python -m scripts.bird_extract_slurm submit \
        --model-backend local \
        --local-model-endpoint-file ../workspace/baselines/pontis/model_slurm/qwen3coder30b-server-65k-extract.endpoint \
        -- --run-id 20260615_extract_local --workers 1 --column-workers 6 california_schools
"""

from __future__ import annotations

import sys

from scripts.bird_job_common import BirdJobSpec, run_cli, split_csv


def extract_projects_from_args(args: list[str]) -> list[str]:
    projects: list[str] = []
    index = 0
    skip_value_for = {"--run-id", "--workers", "--column-workers", "--modules"}
    while index < len(args):
        arg = args[index]
        if arg in skip_value_for:
            index += 2
            continue
        if arg.startswith("--run-id=") or arg.startswith("--workers=") or arg.startswith("--column-workers="):
            index += 1
            continue
        if arg.startswith("--modules="):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        projects.extend(split_csv(arg))
        break
    return projects


SPEC = BirdJobSpec(
    module="scripts.BIRD.extract",
    label="BIRD extract",
    default_job_name="bird-extract",
    default_cpus=48,
    default_mem="192G",
    default_time="24:00:00",
    remainder_name="extract_args",
    default_projects_from_args=extract_projects_from_args,
)


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv, SPEC, __doc__ or "")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
