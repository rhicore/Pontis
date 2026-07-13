"""Compatibility wrapper for CSV top-k generation.

CSV profiling is now a single-pass file-level module implemented in
`csv_column_stats`. This entry point is kept so older pipelines do not fail.
"""

from storage.workspace import Workspace
from extractor.useless.csv_column_stats import generate as _profile_csv_columns


def generate(workspace: Workspace, k: int = 5, file: str | None = None) -> None:
    _profile_csv_columns(workspace, topk_size=k, file=file)


__all__ = ["generate"]
