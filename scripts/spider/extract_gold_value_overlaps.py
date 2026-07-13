#!/usr/bin/env python3
"""Extract physical value-overlap pairs used by Spider2-Snow gold SQL.

This script answers a narrow debugging question: for a gold SQL, which
real database column pairs does each equality join eventually depend on,
after following simple CTE / derived-table lineage.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlglot
from sqlglot import errors, exp
from sqlglot.optimizer.scope import traverse_scope

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from extractor import db_column_overlap as overlap
from scripts.spider.common import (
    SPIDER2_SNOW_CASES,
    SPIDER2_SNOW_DATABASES,
    SPIDER2_SNOW_GOLD_SQL_DIR,
    parse_csv_arg,
)
from scripts.spider.extract_spider2_snow import SPIDER_OVERLAP_KWARGS


@dataclass(frozen=True)
class SourceColumn:
    db_id: str
    schema: str
    table: str
    column: str
    transform: str = "raw"
    via: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return (self.db_id, self.schema, self.table, self.column)

    def ref(self) -> str:
        return f"{self.db_id}.{self.schema}.{self.table}.{self.column}"


@dataclass(frozen=True)
class ExprLineage:
    sources: list[SourceColumn]
    reason: str
    expression_kind: str
    column_count: int


def main() -> None:
    args = _parse_args()
    cases = _load_cases()
    sql_files = _select_sql_files(args, cases)
    options = overlap._resolve_options(None, **SPIDER_OVERLAP_KWARGS)

    occurrence_rows: list[dict] = []
    pair_rows: list[dict] = []
    metadata_cache: dict[str, dict] = {}

    for sql_file in sql_files:
        instance_id = sql_file.stem
        db_id = args.db_id or cases.get(instance_id, {}).get("db_id", "")
        if not db_id:
            occurrence_rows.append({
                "instance_id": instance_id,
                "file": sql_file.name,
                "db_id": "",
                "predicate": "",
                "status": "not_audited",
                "reason": "missing_db_id",
            })
            continue
        db_meta = _load_official_db_metadata(db_id, metadata_cache)
        occurrences, pairs = extract_sql_value_overlaps(
            sql_file,
            db_id=db_id,
            db_meta=db_meta,
            options=options,
            include_where=args.include_where,
            include_unpaired_expressions=args.include_unpaired_expressions,
        )
        occurrence_rows.extend(occurrences)
        pair_rows.extend(pairs)

    _write_outputs(args, occurrence_rows, pair_rows)
    if not args.print_json:
        _print_summary(occurrence_rows, pair_rows)


def extract_sql_value_overlaps(
    sql_file: Path,
    *,
    db_id: str,
    db_meta: dict,
    options: overlap.OverlapOptions,
    include_where: bool = False,
    include_unpaired_expressions: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Return occurrence-level rows and expanded physical-pair rows."""

    instance_id = sql_file.stem
    occurrence_rows: list[dict] = []
    pair_rows: list[dict] = []
    repeated_key_names = (
        _repeated_key_names(db_meta.get("columns", []))
        if db_meta.get("status") == "ok"
        else set()
    )

    try:
        roots = sqlglot.parse(sql_file.read_text(encoding="utf-8"), read="snowflake")
    except Exception as exc:  # pragma: no cover - parser diagnostics only
        occurrence_rows.append({
            "instance_id": instance_id,
            "file": sql_file.name,
            "db_id": db_id,
            "predicate": "",
            "status": "parse_error",
            "reason": type(exc).__name__,
            "detail": str(exc)[:500],
        })
        return occurrence_rows, pair_rows

    for root in roots:
        if root is None:
            continue
        for scope in traverse_scope(root):
            predicates = list(_join_equalities(scope))
            if include_where:
                predicates.extend(_where_equalities(scope))
            for predicate, location in predicates:
                occurrence, pairs = _extract_predicate_overlap(
                    predicate,
                    scope,
                    db_id=db_id,
                    sql_file=sql_file,
                    db_meta=db_meta,
                    repeated_key_names=repeated_key_names,
                    options=options,
                    location=location,
                    include_unpaired_expressions=include_unpaired_expressions,
                )
                occurrence_rows.append(occurrence)
                pair_rows.extend(pairs)
    return occurrence_rows, pair_rows


def _extract_predicate_overlap(
    predicate: exp.EQ,
    scope,
    *,
    db_id: str,
    sql_file: Path,
    db_meta: dict,
    repeated_key_names: set[str],
    options: overlap.OverlapOptions,
    location: str,
    include_unpaired_expressions: bool,
) -> tuple[dict, list[dict]]:
    left = predicate.left
    right = predicate.right
    base = {
        "instance_id": sql_file.stem,
        "file": sql_file.name,
        "db_id": db_id,
        "location": location,
        "predicate": predicate.sql(dialect="snowflake"),
        "left": left.sql(dialect="snowflake"),
        "right": right.sql(dialect="snowflake"),
    }
    if db_meta.get("status") != "ok":
        return {**base, "status": "not_audited", "reason": db_meta.get("status", "")}, []

    try:
        left_lineage = _trace_expr(left, scope, db_id)
        right_lineage = _trace_expr(right, scope, db_id)
    except errors.OptimizeError as exc:
        # A malformed/duplicated alias scope cannot be resolved safely to
        # physical columns. Keep the occurrence for auditability, but do not
        # invent a physical value-domain pair.
        return {
            **base,
            "status": "lineage_error",
            "reason": f"OptimizeError:{exc}",
            "left_expression_kind": "",
            "right_expression_kind": "",
            "left_column_count": "",
            "right_column_count": "",
        }, []
    left_sources = left_lineage.sources
    right_sources = right_lineage.sources
    if not left_sources or not right_sources:
        expression_pair_rows = []
        if include_unpaired_expressions:
            expression_pair_rows.append({
                **base,
                "left_source": _format_sources(left_sources),
                "right_source": _format_sources(right_sources),
                "left_transform": left_lineage.expression_kind,
                "right_transform": right_lineage.expression_kind,
                "left_data_type": "",
                "right_data_type": "",
                "left_sample": "",
                "right_sample": "",
                "pair_status": "not_two_physical_column_lineage",
                "reason": f"{left_lineage.reason} | {right_lineage.reason}".strip(),
            })
        return {
            **base,
            "status": "not_two_physical_column_lineage",
            "reason": f"{left_lineage.reason} | {right_lineage.reason}".strip(),
            "left_expression_kind": left_lineage.expression_kind,
            "right_expression_kind": right_lineage.expression_kind,
            "left_column_count": left_lineage.column_count,
            "right_column_count": right_lineage.column_count,
            "left_sources": _format_sources(left_sources),
            "right_sources": _format_sources(right_sources),
        }, expression_pair_rows

    pair_rows: list[dict] = []
    pair_statuses: list[str] = []
    for left_source in left_sources:
        for right_source in right_sources:
            pair_status, reason, left_column, right_column = _classify_source_pair(
                left_source,
                right_source,
                db_meta=db_meta,
                repeated_key_names=repeated_key_names,
                options=options,
            )
            pair_statuses.append(pair_status)
            pair_rows.append({
                **base,
                "left_source": left_source.ref(),
                "right_source": right_source.ref(),
                "left_expression_kind": left_lineage.expression_kind,
                "right_expression_kind": right_lineage.expression_kind,
                "left_transform": left_source.transform,
                "right_transform": right_source.transform,
                "left_data_type": (left_column or {}).get("data_type", ""),
                "right_data_type": (right_column or {}).get("data_type", ""),
                "left_sample": json.dumps((left_column or {}).get("sample", [])[:5], ensure_ascii=False),
                "right_sample": json.dumps((right_column or {}).get("sample", [])[:5], ensure_ascii=False),
                "pair_status": pair_status,
                "reason": reason,
            })

    if any(status == "filtered" for status in pair_statuses):
        occurrence_status = "filtered"
    elif any(status == "unresolved_official_column" for status in pair_statuses):
        occurrence_status = "unresolved_official_column"
    elif all(status == "same_source_column" for status in pair_statuses):
        occurrence_status = "same_source_column"
    else:
        occurrence_status = "kept_candidate"

    return {
        **base,
        "status": occurrence_status,
        "reason": ",".join(sorted(set(pair_statuses))),
        "left_expression_kind": left_lineage.expression_kind,
        "right_expression_kind": right_lineage.expression_kind,
        "left_column_count": left_lineage.column_count,
        "right_column_count": right_lineage.column_count,
        "left_sources": _format_sources(left_sources),
        "right_sources": _format_sources(right_sources),
        "expanded_pair_count": len(pair_statuses),
    }, pair_rows


def _classify_source_pair(
    left_source: SourceColumn,
    right_source: SourceColumn,
    *,
    db_meta: dict,
    repeated_key_names: set[str],
    options: overlap.OverlapOptions,
) -> tuple[str, str, dict | None, dict | None]:
    if left_source.key() == right_source.key():
        return "same_source_column", "same_source_column", None, None
    left_column, left_reason = _resolve_official_column(db_meta, left_source)
    right_column, right_reason = _resolve_official_column(db_meta, right_source)
    if not left_column or not right_column:
        return "unresolved_official_column", left_reason or right_reason, left_column, right_column
    ok, reason = overlap._should_keep_value_candidate(
        left_column,
        right_column,
        options,
        repeated_key_names,
    )
    return ("kept_candidate" if ok else "filtered"), reason, left_column, right_column


def _join_equalities(scope) -> Iterable[tuple[exp.EQ, str]]:
    for join in scope.expression.args.get("joins") or []:
        on = join.args.get("on")
        if not on:
            continue
        for predicate in on.find_all(exp.EQ):
            yield predicate, "JOIN_ON"


def _where_equalities(scope) -> Iterable[tuple[exp.EQ, str]]:
    where = scope.expression.args.get("where")
    if not where:
        return
    for predicate in where.find_all(exp.EQ):
        yield predicate, "WHERE"


def _trace_expr(expr: exp.Expression, scope, db_id: str, depth: int = 0) -> ExprLineage:
    expression_kind = _transform_kind(expr)
    if depth > 20:
        return ExprLineage([], "depth_limit", expression_kind, 0)
    if isinstance(expr, exp.Alias):
        return _trace_expr(expr.this, scope, db_id, depth + 1)
    if isinstance(expr, exp.Column):
        sources, reason = _trace_column(expr, scope, db_id, depth + 1)
        return ExprLineage(sources, reason, "raw", 1)

    columns = list(expr.find_all(exp.Column))
    if not columns:
        return ExprLineage([], "no_source_column", expression_kind, 0)
    if len(columns) == 1:
        sources, reason = _trace_column(columns[0], scope, db_id, depth + 1)
        if sources and expression_kind != "raw":
            sources = [
                SourceColumn(src.db_id, src.schema, src.table, src.column, expression_kind, src.via)
                for src in sources
            ]
        return ExprLineage(sources, reason, expression_kind, 1)

    source_results: list[SourceColumn] = []
    reasons: list[str] = []
    for column in columns:
        sources, reason = _trace_column(column, scope, db_id, depth + 1)
        if sources:
            source_results.extend(
                SourceColumn(src.db_id, src.schema, src.table, src.column, expression_kind, src.via)
                for src in sources
            )
        elif reason:
            reasons.append(reason)
    return ExprLineage(
        _dedupe_sources(source_results),
        "multi_column_expression" if source_results else "multi_column_expression:" + "|".join(sorted(set(reasons))),
        expression_kind,
        len(columns),
    )


def _trace_column(column: exp.Column, scope, db_id: str, depth: int = 0) -> tuple[list[SourceColumn], str]:
    if depth > 20:
        return [], "depth_limit"
    qualifier = column.table
    if qualifier:
        pair = scope.selected_sources.get(qualifier) or scope.sources.get(qualifier)
        source = pair[1] if isinstance(pair, tuple) else pair
        if isinstance(source, exp.Table):
            table = _table_source(source, db_id)
            return [
                SourceColumn(db_id, table.schema, table.table, column.name, "raw", table.via)
            ], ""
        if source is None:
            return [], "alias_not_found"
        return _trace_output(source, column.name, db_id, depth + 1)

    selected = list(scope.selected_sources.values())
    if len(selected) == 1:
        source = selected[0][1]
        if isinstance(source, exp.Table):
            table = _table_source(source, db_id)
            return [
                SourceColumn(db_id, table.schema, table.table, column.name, "raw", table.via)
            ], ""
        return _trace_output(source, column.name, db_id, depth + 1)
    return [], "unqualified_ambiguous"


def _trace_output(scope, output_name: str, db_id: str, depth: int = 0) -> tuple[list[SourceColumn], str]:
    if depth > 20:
        return [], "depth_limit"
    expression = scope.expression
    if isinstance(expression, exp.Select):
        selected = _select_output_map(scope).get(_norm(output_name))
        if selected is None:
            return [], "output_not_found"
        selected_expr = selected.this if isinstance(selected, exp.Alias) else selected
        lineage = _trace_expr(selected_expr, scope, db_id, depth + 1)
        return lineage.sources, lineage.reason
    if isinstance(expression, exp.Union):
        results: list[SourceColumn] = []
        reasons: list[str] = []
        for union_scope in scope.union_scopes:
            sources, reason = _trace_output(union_scope, output_name, db_id, depth + 1)
            if sources:
                results.extend(sources)
            else:
                reasons.append(reason)
        if results:
            return _dedupe_sources(results), "union"
        return [], "union:" + "|".join(sorted(set(reasons)))
    return [], "unsupported_scope_" + expression.key


def _select_output_map(scope) -> dict[str, exp.Expression]:
    if not isinstance(scope.expression, exp.Select):
        return {}
    outputs: dict[str, exp.Expression] = {}
    for index, selected in enumerate(scope.expression.expressions or []):
        name = selected.alias_or_name
        if name:
            outputs[_norm(name)] = selected
        outputs[f"#{index}"] = selected
    return outputs


def _table_source(source: exp.Table, db_id: str) -> SourceColumn:
    return SourceColumn(
        db_id=db_id,
        schema=source.db or "",
        table=source.name,
        column="",
        transform="raw",
        via=source.sql(dialect="snowflake"),
    )


def _dedupe_sources(sources: Iterable[SourceColumn]) -> list[SourceColumn]:
    seen: set[tuple[str, str, str, str, str, str]] = set()
    result: list[SourceColumn] = []
    for source in sources:
        key = (
            source.db_id,
            source.schema,
            source.table,
            source.column,
            source.transform,
            source.via,
        )
        if key not in seen:
            seen.add(key)
            result.append(source)
    return result


def _transform_kind(expr: exp.Expression) -> str:
    if isinstance(expr, exp.Column):
        return "raw"
    if isinstance(expr, exp.Alias):
        return _transform_kind(expr.this)
    if any(isinstance(node, exp.AggFunc) for node in expr.walk()):
        return "aggregate"
    if any(isinstance(node, exp.Window) for node in expr.walk()):
        return "window"
    if any(isinstance(node, exp.Case) for node in expr.walk()):
        return "case"
    if isinstance(expr, (exp.Cast, exp.TryCast)):
        return "cast"
    if isinstance(expr, exp.Extract):
        return "extract"
    if isinstance(expr, exp.Func):
        return expr.key or "function"
    return expr.key or type(expr).__name__


def _load_official_db_metadata(db_id: str, cache: dict[str, dict]) -> dict:
    if db_id in cache:
        return cache[db_id]
    db_dir = SPIDER2_SNOW_DATABASES / db_id
    if not db_dir.exists():
        cache[db_id] = {"status": "missing_resource", "columns": []}
        return cache[db_id]

    columns: list[dict] = []
    for table_json in sorted(db_dir.glob("*/*.json")):
        with table_json.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        schema_name, table_name = _schema_table_from_json(data, table_json)
        column_names = [str(item) for item in data.get("column_names") or []]
        column_types = [str(item) for item in data.get("column_types") or []]
        samples = _samples_by_column(data.get("sample_rows") or [])
        for index, column_name in enumerate(column_names):
            sample_values = samples.get(_norm(column_name), [])
            lengths = [len(str(value)) for value in sample_values]
            column = {
                "entity_name": f"{db_id}--{schema_name}--{table_name}--{column_name}",
                "db_ref": db_id,
                "table": f"{db_id}--{schema_name}--{table_name}",
                "table_ref": f"{db_id}--{schema_name}--{table_name}",
                "table_name": table_name,
                "schema_name": schema_name,
                "column": column_name,
                "column_ref": f"{db_id}--{schema_name}--{table_name}--{column_name}",
                "data_type": column_types[index] if index < len(column_types) else "",
                "cardinality": 0,
                "min_length": min(lengths) if lengths else None,
                "max_length": max(lengths) if lengths else None,
                "avg_length": None,
                "min_value": None,
                "max_value": None,
                "sample": sample_values,
                "topk": [],
            }
            numeric_values = _numeric_sample_values(sample_values)
            if numeric_values and len(numeric_values) == len(sample_values):
                column["min_value"] = min(numeric_values)
                column["max_value"] = max(numeric_values)
            columns.append(column)

    by_exact: dict[tuple[str, str, str], dict] = {}
    by_table_column: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for column in columns:
        by_exact[(
            _norm(column["schema_name"]),
            _norm(column["table_name"]),
            _norm(column["column"]),
        )] = column
        by_table_column[(
            _norm(column["table_name"]),
            _norm(column["column"]),
        )].append(column)

    cache[db_id] = {
        "status": "ok",
        "columns": columns,
        "by_exact": by_exact,
        "by_table_column": dict(by_table_column),
    }
    return cache[db_id]


def _schema_table_from_json(data: dict, path: Path) -> tuple[str, str]:
    full_name = str(data.get("table_fullname") or "")
    parts = full_name.split(".")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1]
    rel = path.relative_to(SPIDER2_SNOW_DATABASES)
    if len(rel.parts) >= 3:
        return rel.parts[1], path.stem
    return "", path.stem


def _samples_by_column(sample_rows: list) -> dict[str, list[str]]:
    samples: dict[str, list[str]] = defaultdict(list)
    for row in sample_rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            text = _sample_text(value)
            if text is not None and len(samples[_norm(key)]) < 20:
                samples[_norm(key)].append(text)
    return dict(samples)


def _sample_text(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text in {"", "\\N", "NULL", "None"}:
        return None
    return text


def _numeric_sample_values(values: list[str]) -> list[float]:
    numbers: list[float] = []
    for value in values:
        try:
            numbers.append(float(str(value).strip()))
        except (TypeError, ValueError):
            pass
    return numbers


def _resolve_official_column(db_meta: dict, source: SourceColumn) -> tuple[dict | None, str]:
    key = (_norm(source.schema), _norm(source.table), _norm(source.column))
    column = db_meta.get("by_exact", {}).get(key)
    if column:
        return column, ""
    candidates = db_meta.get("by_table_column", {}).get((_norm(source.table), _norm(source.column)), [])
    if len(candidates) == 1:
        return candidates[0], ""
    if len(candidates) > 1:
        return None, "ambiguous_official_column"
    return None, "missing_official_column"


def _repeated_key_names(columns: Iterable[dict]) -> set[str]:
    units_by_key: dict[str, set[str]] = defaultdict(set)
    for column in columns:
        name = overlap._normalized_key_name(column.get("column", ""))
        if name:
            units_by_key[name].add(str(column.get("table") or ""))
    return {name for name, units in units_by_key.items() if len(units) >= 2}


def _format_sources(sources: Iterable[SourceColumn]) -> str:
    return " | ".join(f"{source.ref()}[{source.transform}]" for source in sources)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _load_cases() -> dict[str, dict]:
    cases: dict[str, dict] = {}
    if not SPIDER2_SNOW_CASES.exists():
        return cases
    for line in SPIDER2_SNOW_CASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases[str(row["instance_id"])] = row
    return cases


def _select_sql_files(args: argparse.Namespace, cases: dict[str, dict]) -> list[Path]:
    if args.sql_file:
        return [Path(args.sql_file).resolve()]
    sql_files = sorted(SPIDER2_SNOW_GOLD_SQL_DIR.glob("*.sql"))
    instance_filter = set(parse_csv_arg(args.instances) or [])
    db_filter = set(parse_csv_arg(args.db) or [])
    if instance_filter:
        sql_files = [path for path in sql_files if path.stem in instance_filter]
    if db_filter:
        sql_files = [
            path for path in sql_files
            if cases.get(path.stem, {}).get("db_id") in db_filter
        ]
    if args.limit is not None:
        sql_files = sql_files[: args.limit]
    return sql_files


def _write_outputs(args: argparse.Namespace, occurrence_rows: list[dict], pair_rows: list[dict]) -> None:
    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(output_dir / "gold_value_overlap_occurrences.csv", occurrence_rows)
        _write_csv(output_dir / "gold_value_overlap_pairs.csv", pair_rows)
        (output_dir / "gold_value_overlap_summary.json").write_text(
            json.dumps(_summary_dict(occurrence_rows, pair_rows), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.print_json:
        print(json.dumps({
            "summary": _summary_dict(occurrence_rows, pair_rows),
            "occurrences": occurrence_rows,
            "pairs": pair_rows,
        }, ensure_ascii=False, indent=2))


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _summary_dict(occurrence_rows: list[dict], pair_rows: list[dict]) -> dict:
    distinct_pair_rows = [row for row in pair_rows if row.get("pair_status") != "same_source_column"]
    unique_pairs = {
        tuple(sorted((row.get("left_source", ""), row.get("right_source", ""))))
        for row in distinct_pair_rows
    }
    unique_filtered = {
        tuple(sorted((row.get("left_source", ""), row.get("right_source", ""))))
        for row in distinct_pair_rows
        if row.get("pair_status") == "filtered"
    }
    return {
        "occurrences": len(occurrence_rows),
        "occurrence_status": dict(Counter(row.get("status", "") for row in occurrence_rows)),
        "expanded_pairs": len(pair_rows),
        "pair_status": dict(Counter(row.get("pair_status", "") for row in pair_rows)),
        "distinct_expanded_pairs": len(distinct_pair_rows),
        "unique_distinct_pairs": len(unique_pairs),
        "unique_filtered_pairs": len(unique_filtered),
        "filtered_pair_reasons": dict(Counter(
            row.get("reason", "")
            for row in distinct_pair_rows
            if row.get("pair_status") == "filtered"
        )),
    }


def _print_summary(occurrence_rows: list[dict], pair_rows: list[dict]) -> None:
    summary = _summary_dict(occurrence_rows, pair_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract physical value-overlap pairs from Spider2-Snow gold SQL."
    )
    parser.add_argument("--instances", help="Comma-separated Spider2-Snow instance ids, e.g. sf002,sf_bq043")
    parser.add_argument("--db", help="Comma-separated db ids to scan from the gold SQL set")
    parser.add_argument("--sql-file", help="Path to one SQL file. Use --db-id if the stem is not a Spider2 instance id.")
    parser.add_argument("--db-id", help="Database id for --sql-file or override for selected files")
    parser.add_argument("--limit", type=int, help="Limit selected gold SQL files after filtering")
    parser.add_argument("--include-where", action="store_true", help="Also extract equality predicates from WHERE clauses")
    parser.add_argument(
        "--include-unpaired-expressions",
        action="store_true",
        help="Also write pair rows for expression comparisons that cannot be reduced to two physical column sides.",
    )
    parser.add_argument("--output-dir", help="Write CSV and summary JSON into this directory")
    parser.add_argument("--print-json", action="store_true", help="Print full occurrence and pair rows as JSON")
    return parser.parse_args()


if __name__ == "__main__":
    main()
