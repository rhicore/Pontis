"""Command-line interface for shared SQL result evaluation."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Sequence

from scripts.result_evaluation import (
    ComparisonPolicy,
    GoldenResult,
    compare_result_sets,
    load_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare one predicted CSV/JSON result against one or more gold alternatives.",
    )
    parser.add_argument("predicted", type=Path)
    parser.add_argument("--golden", action="append", required=True, type=Path)
    parser.add_argument(
        "--condition-cols",
        action="append",
        default=[],
        metavar="I,J,...",
        help="Required zero-based gold columns; repeat once per --golden",
    )
    parser.add_argument("--ordered", action="store_true")
    parser.add_argument("--allow-extra-predicted-columns", action="store_true")
    parser.add_argument("--no-column-reorder", action="store_true")
    parser.add_argument("--numeric-tolerance", default="0")
    parser.add_argument("--parse-numeric-strings", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tolerance = Decimal(args.numeric_tolerance)
        if not tolerance.is_finite() or tolerance < 0:
            raise ValueError("numeric tolerance must be finite and non-negative")
        conditions = _conditions(args.condition_cols, len(args.golden))
        predicted = load_result(args.predicted)
        goldens = [
            GoldenResult(
                load_result(path),
                required_columns=conditions[index],
                name=path.name,
            )
            for index, path in enumerate(args.golden)
        ]
        comparison = compare_result_sets(
            predicted,
            goldens,
            policy=ComparisonPolicy(
                ordered=args.ordered,
                allow_extra_predicted_columns=args.allow_extra_predicted_columns,
                allow_column_reorder=not args.no_column_reorder,
                parse_numeric_strings=args.parse_numeric_strings,
                numeric_abs_tolerance=tolerance,
            ),
        )
    except (OSError, ValueError, json.JSONDecodeError, InvalidOperation) as exc:
        print(json.dumps({"business_correct": False, "match_type": "invalid_input", "error": str(exc)}))
        return 2

    print(json.dumps(comparison.as_dict(), ensure_ascii=False, default=str))
    return 0 if comparison.business_correct else 1


def _conditions(raw: Sequence[str], count: int) -> list[tuple[int, ...] | None]:
    if not raw:
        return [None] * count
    if len(raw) != count:
        raise ValueError("--condition-cols must be omitted or repeated once per --golden")
    return [tuple(int(item.strip()) for item in value.split(",") if item.strip()) for value in raw]
