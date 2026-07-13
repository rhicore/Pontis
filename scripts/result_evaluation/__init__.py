"""Shared relation-preserving result evaluation for SQL benchmarks."""

from scripts.result_evaluation.core import (
    ComparisonPolicy,
    GoldenResult,
    ResultComparison,
    ResultTable,
    compare_result_sets,
)
from scripts.result_evaluation.io import load_result

__all__ = [
    "ComparisonPolicy",
    "GoldenResult",
    "ResultComparison",
    "ResultTable",
    "compare_result_sets",
    "load_result",
]
