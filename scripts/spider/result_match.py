"""Spider2-Snow adapter for the shared relation-preserving result evaluator."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from scripts.result_evaluation import (
    ComparisonPolicy,
    GoldenResult,
    ResultComparison,
    compare_result_sets,
    load_result,
)


SPIDER_NUMERIC_TOLERANCE = Decimal("0.01")


def load_spider_eval_config(path: Path) -> dict[str, dict[str, Any]]:
    config: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            instance_id = str(row["instance_id"])
            if instance_id in config:
                raise ValueError(f"duplicate instance_id {instance_id!r} at line {line_number}")
            config[instance_id] = row
    return config


def discover_spider_gold_paths(instance_id: str, gold_result_dir: Path) -> list[Path]:
    """Follow Spider's base-file-or-suffixed-alternatives convention."""

    base = gold_result_dir / f"{instance_id}.csv"
    if base.exists():
        return [base]
    return sorted(gold_result_dir.glob(f"{instance_id}_[a-z].csv"))


def compare_spider_result_files(
    predicted_path: Path,
    gold_paths: Sequence[Path],
    config: dict[str, Any],
) -> ResultComparison:
    if not predicted_path.exists():
        return ResultComparison(
            False,
            "missing_prediction_result",
            details={"predicted_path": str(predicted_path)},
        )
    if not gold_paths:
        return ResultComparison(False, "missing_gold")

    conditions = _condition_columns(config.get("condition_cols"), len(gold_paths))
    ordered = _ordered_flags(config.get("ignore_order", True), len(gold_paths))
    goldens = [
        GoldenResult(
            _load_result_cached(path),
            required_columns=conditions[index],
            ordered=ordered[index],
            name=path.name,
        )
        for index, path in enumerate(gold_paths)
    ]
    return compare_result_sets(
        _load_result_cached(predicted_path),
        goldens,
        policy=ComparisonPolicy(
            allow_extra_predicted_columns=True,
            allow_column_reorder=True,
            max_reordered_columns=8,
            trim_strings=True,
            parse_numeric_strings=True,
            numeric_abs_tolerance=SPIDER_NUMERIC_TOLERANCE,
        ),
    )


@lru_cache(maxsize=2048)
def _load_result_cached(path: Path):
    return load_result(path)


def evaluate_spider_result_directory(
    *,
    predicted_result_dir: Path,
    gold_result_dir: Path,
    eval_config_path: Path,
    instance_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    config = load_spider_eval_config(eval_config_path)
    selected = sorted(set(instance_ids) if instance_ids is not None else config)
    rows: list[dict[str, Any]] = []
    for instance_id in selected:
        standard = config.get(instance_id)
        if standard is None:
            comparison = ResultComparison(False, "missing_eval_config")
            gold_paths: list[Path] = []
        else:
            gold_paths = discover_spider_gold_paths(instance_id, gold_result_dir)
            try:
                comparison = compare_spider_result_files(
                    predicted_result_dir / f"{instance_id}.csv",
                    gold_paths,
                    standard,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                comparison = ResultComparison(
                    False,
                    "invalid_result_input",
                    details={"error": str(exc)},
                )
        rows.append(
            {
                "instance_id": instance_id,
                **comparison.as_dict(),
                "gold_paths": [str(path) for path in gold_paths],
            }
        )
    return rows


def _condition_columns(raw: Any, count: int) -> list[tuple[int, ...] | None]:
    if raw is None or raw == [] or raw == [[]] or raw == [None]:
        return [None] * count
    if not isinstance(raw, (list, tuple)):
        raw = [raw]

    nested = all(isinstance(item, (list, tuple)) for item in raw)
    if nested:
        configured = [tuple(int(index) for index in item) or None for item in raw[:count]]
        # Some published Spider configs contain fewer condition lists than gold
        # CSVs. Requiring every column for the unconfigured alternatives is the
        # conservative fallback; copying another alternative's contract is not.
        return configured + [None] * (count - len(configured))

    shared = tuple(int(index) for index in raw)
    return [shared or None for _ in range(count)]


def _ordered_flags(ignore_order: Any, count: int) -> list[bool]:
    if isinstance(ignore_order, (list, tuple)):
        if len(ignore_order) != count:
            raise ValueError("ignore_order list must match the number of golden results")
        return [not bool(value) for value in ignore_order]
    return [not bool(ignore_order)] * count
