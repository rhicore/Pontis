"""Compatibility wrapper for CSV sample generation.

CSV profiling is now a single-pass file-level module implemented in
`csv_column_stats`. This entry point is kept so older pipelines do not fail.
"""

from storage.workspace import Workspace
from extractor.modules.csv_column_stats import generate as _profile_csv_columns


def generate(workspace: Workspace, sample_size: int = 10, file: str | None = None) -> None:
    _profile_csv_columns(workspace, sample_size=sample_size, file=file)


__all__ = ["generate"]
