"""Result-table loaders shared by benchmark adapters and the CLI."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from scripts.result_evaluation.core import ResultTable


def load_result(path: Path | str) -> ResultTable:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return _load_csv(path)
    if path.suffix.lower() == ".json":
        return _load_json(path)
    raise ValueError(f"unsupported result format: {path.suffix or '(none)'}; use CSV or JSON")


def _load_csv(path: Path) -> ResultTable:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            columns = tuple(next(reader))
        except StopIteration as exc:
            raise ValueError(f"empty CSV: {path}") from exc
        # pandas.read_csv, used by Spider's official evaluator, skips physically
        # blank lines and pads short records with NaN. Preserve rows such as
        # `,,`, which still contain fields, and represent padding as None.
        rows = tuple(
            tuple(row) + (None,) * (len(columns) - len(row))
            for row in reader
            if row
        )
    return _validated(ResultTable(columns, rows), path)


def _load_json(path: Path) -> ResultTable:
    data: Any
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict) and "columns" in data and "rows" in data:
        table = ResultTable(
            tuple(str(column) for column in data["columns"]),
            tuple(tuple(row) for row in data["rows"]),
        )
        return _validated(table, path)

    if isinstance(data, list) and data and all(isinstance(row, dict) for row in data):
        columns = tuple(str(column) for column in data[0].keys())
        expected = set(columns)
        rows = []
        for index, row in enumerate(data):
            if set(row) != expected:
                raise ValueError(f"JSON record {index} has different columns")
            rows.append(tuple(row[column] for column in columns))
        return _validated(ResultTable(columns, tuple(rows)), path)

    raise ValueError(
        "JSON must be {\"columns\": [...], \"rows\": [[...]]} "
        "or a non-empty list of records"
    )


def _validated(table: ResultTable, path: Path) -> ResultTable:
    for index, row in enumerate(table.rows):
        if len(row) != table.width:
            raise ValueError(
                f"row {index} in {path} has {len(row)} values; expected {table.width}"
            )
    return table
