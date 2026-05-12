"""Compatibility wrapper for DB column TopK generation.

TopK is now produced by db_column_stats_approx in the same single-pass profile.
"""

from extractor.modules.db_column_stats_approx import generate as _generate_profile


def generate(workspace, k: int = 5) -> None:
    _generate_profile(workspace, topk_size=k)
