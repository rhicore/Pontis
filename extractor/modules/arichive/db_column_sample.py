"""Compatibility wrapper for DB column sample generation.

Sampling is now produced by db_column_stats_approx in the same single-pass profile.
"""

from extractor.modules.db_column_stats_approx import generate as _generate_profile


def generate(workspace, sample_size: int = 10) -> None:
    _generate_profile(workspace, sample_size=sample_size)
