#!/usr/bin/env python3
"""Write exact value-overlap metrics for Spider2-Snow gold join pairs."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
if str(PONTIS_ROOT) not in sys.path:
    sys.path.insert(0, str(PONTIS_ROOT))

from scripts.spider.audit_overlap_pipeline import _snowflake_db_connect


DEFAULT_INPUT = (
    TEXT2SQL_ROOT
    / "workspace/baselines/pontis/analysis/spider2_snow"
    / "gold_value_overlap_lineage_current/gold_value_overlap_pairs.csv"
)
DEFAULT_OUTPUT = PONTIS_ROOT / "docs/spider_problem/spider2_snow_gold_join_overlap_metrics.md"

# These full DISTINCT joins exceeded the sponsored warehouse's 120-second
# statement timeout during the 2026-07-10 audit. They remain in the report and
# can be retried explicitly if the warehouse limit changes.
KNOWN_TIMEOUT_PAIRS = {
    tuple(sorted((
        "FINANCE__ECONOMICS.CYBERSYN.FINANCIAL_INSTITUTION_ENTITIES.ID_RSSD",
        "FINANCE__ECONOMICS.CYBERSYN.FINANCIAL_INSTITUTION_TIMESERIES.ID_RSSD",
    ))),
    tuple(sorted((
        "GOOGLE_TRENDS.GOOGLE_TRENDS.TOP_RISING_TERMS.refresh_date",
        "GOOGLE_TRENDS.GOOGLE_TRENDS.INTERNATIONAL_TOP_RISING_TERMS.refresh_date",
    ))),
    tuple(sorted((
        "GOOGLE_TRENDS.GOOGLE_TRENDS.TOP_RISING_TERMS.week",
        "GOOGLE_TRENDS.GOOGLE_TRENDS.INTERNATIONAL_TOP_RISING_TERMS.week",
    ))),
}


@dataclass(frozen=True)
class Metric:
    left_cardinality: int | None
    right_cardinality: int | None
    intersection: int | None
    overlap_min: float | None
    jaccard: float | None
    status: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retry-timeouts", action="store_true")
    args = parser.parse_args()

    rows = _load_unique_pairs(args.input)
    metrics = _measure_pairs(rows, max(1, args.workers), retry_timeouts=args.retry_timeouts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_render_report(rows, metrics), encoding="utf-8")
    print(f"Wrote {args.output} ({len(rows)} pairs)")
    return 0


def _load_unique_pairs(path: Path) -> list[dict[str, str]]:
    unique: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            left = row.get("left_source") or ""
            right = row.get("right_source") or ""
            if not left or not right or left == right or row.get("pair_status") == "same_source_column":
                continue
            unique.setdefault(tuple(sorted((left, right))), row)
    return sorted(unique.values(), key=lambda row: (row["db_id"], row["file"], row["predicate"]))


def _measure_pairs(
    rows: list[dict[str, str]],
    workers: int,
    *,
    retry_timeouts: bool,
) -> dict[tuple[str, str], Metric]:
    by_db: dict[str, list[dict[str, str]]] = defaultdict(list)
    metrics: dict[tuple[str, str], Metric] = {}
    for row in rows:
        key = _pair_key(row)
        if key in KNOWN_TIMEOUT_PAIRS and not retry_timeouts:
            metrics[key] = Metric(None, None, None, None, None, "statement_timeout")
            continue
        by_db[row["db_id"]].append(row)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_measure_database, db_id, db_rows): db_id
            for db_id, db_rows in by_db.items()
        }
        for future in as_completed(futures):
            metrics.update(future.result())
    return metrics


def _measure_database(db_id: str, rows: list[dict[str, str]]) -> dict[tuple[str, str], Metric]:
    handle = _snowflake_db_connect(db_id)
    connection = handle(readonly=True)
    measured: dict[tuple[str, str], Metric] = {}
    try:
        cursor = connection.cursor()
        for row in rows:
            key = _pair_key(row)
            try:
                cursor.execute(_metric_sql(row["left_source"], row["right_source"]))
                left_card, right_card, intersection = (int(value) for value in cursor.fetchone())
                minimum = min(left_card, right_card)
                union = left_card + right_card - intersection
                measured[key] = Metric(
                    left_card,
                    right_card,
                    intersection,
                    intersection / minimum if minimum else 0.0,
                    intersection / union if union else 0.0,
                    "exact",
                )
            except Exception as exc:
                measured[key] = Metric(None, None, None, None, None, f"error: {type(exc).__name__}")
    finally:
        connection.close()
    return measured


def _metric_sql(left_source: str, right_source: str) -> str:
    left_db, left_schema, left_table, left_column = left_source.split(".", 3)
    right_db, right_schema, right_table, right_column = right_source.split(".", 3)
    left_expr = f"LOWER(TRIM(TO_VARCHAR({_quote(left_column)})))"
    right_expr = f"LOWER(TRIM(TO_VARCHAR({_quote(right_column)})))"
    return f"""WITH l AS (
  SELECT DISTINCT {left_expr} AS v
  FROM {_quote(left_db)}.{_quote(left_schema)}.{_quote(left_table)}
  WHERE {_quote(left_column)} IS NOT NULL AND {left_expr} <> ''
), r AS (
  SELECT DISTINCT {right_expr} AS v
  FROM {_quote(right_db)}.{_quote(right_schema)}.{_quote(right_table)}
  WHERE {_quote(right_column)} IS NOT NULL AND {right_expr} <> ''
)
SELECT
  (SELECT COUNT(*) FROM l),
  (SELECT COUNT(*) FROM r),
  (SELECT COUNT(*) FROM l INNER JOIN r USING (v))"""


def _render_report(rows: list[dict[str, str]], metrics: dict[tuple[str, str], Metric]) -> str:
    exact = [metrics[_pair_key(row)] for row in rows if metrics[_pair_key(row)].status == "exact"]
    positive = [metric for metric in exact if (metric.overlap_min or 0.0) > 0.0]
    raw_positive = [
        metrics[_pair_key(row)]
        for row in rows
        if metrics[_pair_key(row)].status == "exact"
        and (metrics[_pair_key(row)].overlap_min or 0.0) > 0.0
        and row.get("left_transform") == "raw"
        and row.get("right_transform") == "raw"
    ]
    lines = [
        "# Spider2-Snow Gold Join Value-Overlap Audit",
        "",
        "This report measures the 127 unique physical-column pairs extracted from the local Spider2-Snow gold SQL lineage.",
        "Values are normalized with `LOWER(TRIM(TO_VARCHAR(value)))` before exact distinct-set comparison.",
        "",
        "## Summary",
        "",
        f"- Gold physical-column pairs: {len(rows)}",
        f"- Exact measurements: {len(exact)}",
        f"- Statement timeouts/errors: {len(rows) - len(exact)}",
        f"- Exact pairs with non-empty intersection: {len(positive)}",
        f"- Exact pairs with zero intersection: {len(exact) - len(positive)}",
        f"- Lowest positive `overlap/min`: {_format_metric(min((m.overlap_min for m in positive), default=None))}",
        f"- Lowest positive raw/raw `overlap/min`: {_format_metric(min((m.overlap_min for m in raw_positive), default=None))}",
        "",
        "### Overlap/min distribution",
        "",
    ]
    for label, count in _distribution(metric.overlap_min for metric in exact):
        lines.append(f"- `{label}`: {count}")
    lines.extend(["", "### Jaccard distribution", ""])
    for label, count in _distribution(metric.jaccard for metric in exact):
        lines.append(f"- `{label}`: {count}")

    lines.extend([
        "",
        "## All Gold Pairs",
        "",
        "| # | DB / SQL | Predicate | Lineage | Left cardinality | Right cardinality | Intersection | Overlap/min | Jaccard | Status |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ])
    ordered = sorted(
        rows,
        key=lambda row: (
            metrics[_pair_key(row)].overlap_min is None,
            metrics[_pair_key(row)].overlap_min if metrics[_pair_key(row)].overlap_min is not None else 2.0,
            row["db_id"],
            row["file"],
        ),
    )
    for index, row in enumerate(ordered, start=1):
        metric = metrics[_pair_key(row)]
        lineage = f"{row.get('left_transform') or '?'} / {row.get('right_transform') or '?'}"
        lines.append(
            "| " + " | ".join([
                str(index),
                _escape(f"{row['db_id']} / {row['file']}"),
                _escape(f"`{row['predicate']}`"),
                _escape(lineage),
                _format_int(metric.left_cardinality),
                _format_int(metric.right_cardinality),
                _format_int(metric.intersection),
                _format_metric(metric.overlap_min),
                _format_metric(metric.jaccard),
                _escape(metric.status),
            ]) + " |"
        )
        lines.append(
            f"<!-- left={row['left_source']} right={row['right_source']} pair_status={row['pair_status']} -->"
        )

    lines.extend([
        "",
        "## Interpretation Notes",
        "",
        "- `overlap/min = |A intersection B| / min(|A|, |B|)` is the Pontis target metric.",
        "- Jaccard can be very small even when `overlap/min` is high if the two column cardinalities are strongly skewed.",
        "- Expression lineage such as `coalesce`, `cast`, or `extract` expands to physical source columns; its full-column metric is not necessarily the runtime expression-domain metric.",
        "- A zero result means the current hosted Snowflake snapshot has no normalized physical-value intersection. It does not by itself prove that the gold SQL lineage extraction is semantically correct.",
        "",
    ])
    return "\n".join(lines)


def _distribution(values) -> list[tuple[str, int]]:
    counter = Counter(_bucket(value) for value in values if value is not None)
    order = ["zero", "(0, 0.0001)", "[0.0001, 0.001)", "[0.001, 0.01)", "[0.01, 0.05)", "[0.05, 0.1)", "[0.1, 0.5)", "[0.5, 1]"]
    return [(label, counter[label]) for label in order]


def _bucket(value: float) -> str:
    if value == 0:
        return "zero"
    if value < 0.0001:
        return "(0, 0.0001)"
    if value < 0.001:
        return "[0.0001, 0.001)"
    if value < 0.01:
        return "[0.001, 0.01)"
    if value < 0.05:
        return "[0.01, 0.05)"
    if value < 0.1:
        return "[0.05, 0.1)"
    if value < 0.5:
        return "[0.1, 0.5)"
    return "[0.5, 1]"


def _pair_key(row: dict[str, str]) -> tuple[str, str]:
    return tuple(sorted((row["left_source"], row["right_source"])))


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _format_metric(value: float | None) -> str:
    return "-" if value is None else f"{value:.9g}"


def _format_int(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _escape(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
