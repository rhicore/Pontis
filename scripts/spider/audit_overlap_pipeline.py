#!/usr/bin/env python3
"""Audit Spider2-Snow overlap candidates and gold-join recall."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from extractor import db_column_overlap as overlap
from extractor import db_table_group
from extractor.spider2_snow_schema import _load_official_schema
from extractor.utils.overlap_candidates import _iter_pipeline_candidate_pairs, _prepare_pipeline_comparison_units
from extractor.utils.overlap_filter_pipeline import (
    FILTER_RUNNERS,
    FilterContext,
    OverlapCandidate,
    resolved_filter_pipeline,
)
from scripts.preprocess_engine import init_workspace
from scripts.spider.common import (
    SPIDER2_SNOW_DATABASES,
    SPIDER2_SNOW_CREDENTIAL,
    ensure_spider2_snow_neo4j,
    get_project_dir,
    get_projects_root,
    load_spider2_snow_cases,
)
from scripts.spider.extract_spider2_snow import SPIDER_OVERLAP_DB_OVERRIDES, SPIDER_OVERLAP_KWARGS
from storage.stores.access import DbConnect


ANALYSIS_DIR = TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis" / "analysis" / "spider2_snow"
GOLD_PAIR_CSV = ANALYSIS_DIR / "gold_value_overlap_lineage_after_shape_fix" / "gold_value_overlap_pairs.csv"


def main() -> int:
    args = _parse_args()
    if args.source == "project":
        ensure_spider2_snow_neo4j()
    overlap_kwargs = dict(SPIDER_OVERLAP_KWARGS)
    if args.value_match_method:
        overlap_kwargs["value_match_method"] = args.value_match_method
    if args.pre_value_only:
        overlap_kwargs["filter_pipeline"] = [
            stage
            for stage in overlap_kwargs.get("filter_pipeline", [])
            if str(stage.get("name") or "") != "value_overlap"
        ]
    options = overlap._resolve_options(None, **overlap_kwargs)
    gold_pairs = _load_gold_pairs(Path(args.gold_pair_csv) if args.gold_pair_csv else GOLD_PAIR_CSV)
    gold_sql_dbs = _load_gold_sql_dbs()
    db_ids = _select_db_ids(args, gold_sql_dbs)

    out_dir = Path(args.output_dir or (ANALYSIS_DIR / "overlap_pipeline_audit"))
    out_dir.mkdir(parents=True, exist_ok=True)
    db_rows: list[dict] = []
    pre_value_rows: list[dict] = []
    overlap_rows: list[dict] = []

    for db_id in db_ids:
        db_options = replace(options, **SPIDER_OVERLAP_DB_OVERRIDES.get(db_id, {}))
        started = time.time()
        row = {
            "db_id": db_id,
            "status": "ok",
            "tables": 0,
            "physical_columns": 0,
            "column_domains": 0,
            "multi_column_domains": 0,
            "pre_value_candidates": 0,
            "overlap_candidates": 0,
            "candidate_seconds": 0.0,
            "value_seconds": 0.0,
            "gold_sql_db": db_id in gold_sql_dbs,
            "gold_pairs": len({pair for gold_db, pair in gold_pairs if gold_db == db_id}),
            "gold_recalled": 0,
            "gold_missed": 0,
            "gold_recall": "",
            "lazo_estimated": 0,
            "lazo_uncertain": 0,
            "lazo_profile_unavailable": 0,
            "filter_stages": "",
            "error": "",
        }
        try:
            if args.source == "official":
                result = _run_official_db(db_id, db_options)
            elif args.source == "snowflake":
                result = _run_snowflake_schema_db(db_id, db_options)
            else:
                result = _run_project_db(db_id, db_options)
            db_overlaps = result["overlaps"]
            db_pre_pairs = result["candidates"]
            if not args.pre_value_only and not args.counts_only:
                pre_value_rows.extend(_candidate_rows(db_id, db_pre_pairs))
            if not args.counts_only:
                overlap_rows.extend(_overlap_rows(db_id, db_overlaps))
            db_gold_pairs = {pair for gold_db, pair in gold_pairs if gold_db == db_id}
            recalled = _recalled_gold_count_from_overlaps(db_gold_pairs, db_overlaps)
            pre_recalled = _recalled_gold_count_from_candidates(db_gold_pairs, db_pre_pairs)
            decisions = Counter(
                str((item.get("stats") or {}).get("decision") or "")
                for item in db_overlaps
            )
            row.update({
                "tables": result["tables"],
                "physical_columns": result["physical_columns"],
                "column_domains": result["stats"].get("column_domain_count", 0),
                "multi_column_domains": result["stats"].get("multi_column_domain_count", 0),
                "pre_value_candidates": result["pre_value_candidate_count"],
                "overlap_candidates": len(db_overlaps),
                "candidate_seconds": round(result["candidate_seconds"], 3),
                "value_seconds": round(result["value_seconds"], 3),
                "gold_recalled": recalled,
                "gold_missed": len(db_gold_pairs) - recalled,
                "gold_recall": round(recalled / len(db_gold_pairs), 6) if db_gold_pairs else "",
                "gold_pre_value_recalled": pre_recalled,
                "gold_pre_value_missed": len(db_gold_pairs) - pre_recalled,
                "gold_pre_value_recall": round(pre_recalled / len(db_gold_pairs), 6) if db_gold_pairs else "",
                "lazo_estimated": decisions["estimated_above_threshold"],
                "lazo_uncertain": decisions["uncertain_retained"],
                "lazo_profile_unavailable": decisions["profile_unavailable_retained"],
                "filter_stages": json.dumps(result["stats"].get("filter_pipeline", {}), ensure_ascii=False, sort_keys=True),
            })
        except Exception as exc:
            if args.fail_fast:
                raise
            row["status"] = "error"
            row["error"] = repr(exc)
        row["total_seconds"] = round(time.time() - started, 3)
        db_rows.append(row)
        print(row, flush=True)

    db_csv = out_dir / "db_overlap_counts.csv"
    pre_value_csv = out_dir / "pre_value_candidates.csv"
    overlap_csv = out_dir / "overlap_candidates.csv"
    _write_csv(db_csv, db_rows)
    _write_csv(pre_value_csv, pre_value_rows)
    _write_csv(overlap_csv, overlap_rows)
    summary = _summary(db_rows, pre_value_rows, overlap_rows, gold_pairs, gold_sql_dbs)
    summary["db_csv"] = str(db_csv)
    summary["pre_value_csv"] = str(pre_value_csv)
    summary["overlap_csv"] = str(overlap_csv)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("OUT", out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("project", "official", "snowflake"),
        default="project",
        help="project reads prepared Pontis/Neo4j projects; official reads Spider2 resource files; snowflake reads live INFORMATION_SCHEMA.",
    )
    parser.add_argument("--gold-only", action="store_true", help="Only run databases appearing in local gold SQL lineage.")
    parser.add_argument("--db", help="Comma-separated db_id list.")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--pre-value-only",
        action="store_true",
        help="Stop after cheap filters and report the number of pairs that would enter value matching.",
    )
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="Write only per-database counts and recall; do not retain detailed candidate rows in memory.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Abort immediately if any database/profile query fails; never continue with partial counts.",
    )
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--gold-pair-csv",
        help="Physical Gold value-overlap pairs emitted by extract_gold_value_overlaps.py.",
    )
    parser.add_argument(
        "--value-match-method",
        choices=("sql", "minhash", "minhash_then_sql", "snowflake_minhash", "snowflake_lazo", "hash_index", "sample_bloom", "sample_bloom_then_sql", "metadata_sample"),
        help="Override db_column_overlap value matching method.",
    )
    return parser.parse_args()


def _select_db_ids(args: argparse.Namespace, gold_sql_dbs: list[str]) -> list[str]:
    if args.db:
        db_ids = [item.strip() for item in args.db.split(",") if item.strip()]
    elif args.gold_only:
        db_ids = gold_sql_dbs
    elif args.source in {"official", "snowflake"}:
        db_ids = sorted(path.name for path in SPIDER2_SNOW_DATABASES.iterdir() if path.is_dir())
    else:
        db_ids = sorted(path.name for path in get_projects_root().iterdir() if path.is_dir())
    if args.limit is not None:
        db_ids = db_ids[: args.limit]
    return db_ids


def _run_project_db(db_id: str, options: overlap.OverlapOptions) -> dict:
    workspace, _config = init_workspace(str(get_project_dir(db_id)))
    db_rows = workspace.cypher(
        """
        MATCH (d:db)
        WITH d, coalesce(d._db_connect, d.db_connect) AS db_connect
        WHERE (d._ref IS NOT NULL OR d.name IS NOT NULL) AND db_connect IS NOT NULL
        RETURN d, db_connect
        LIMIT 1
        """
    )
    if not db_rows:
        raise RuntimeError("missing db node with db_connect")
    db_node = db_rows[0]["d"]
    db_connect = db_rows[0]["db_connect"]
    db_ref = str(db_node.get("_ref") or db_node.get("name") or db_id)
    dialect = str(getattr(db_connect, "dialect", "") or db_node.get("dialect") or "snowflake").lower()
    columns = overlap._load_db_columns(workspace, db_ref)
    table_columns: dict[str, list[dict]] = defaultdict(list)
    for col in columns:
        table_columns[col["table"]].append(col)
    memberships = overlap._load_table_group_memberships(
        workspace,
        table_names=table_columns.keys(),
        table_refs=(col.get("table_ref") for col in columns),
    )
    if "value_overlap" not in {item.name for item in options.filter_pipeline}:
        t0 = time.time()
        pre_value_candidate_count, stats, filter_stats = overlap.count_pre_value_candidates(
            table_columns,
            options=options,
            table_group_memberships=memberships,
        )
        t1 = time.time()
        stats["filter_pipeline"] = filter_stats
        return {
            "tables": len(table_columns),
            "physical_columns": len(columns),
            "stats": stats,
            "overlaps": [],
            "candidates": [],
            "pre_value_candidate_count": pre_value_candidate_count,
            "candidate_seconds": t1 - t0,
            "value_seconds": 0.0,
        }

    t0 = time.time()
    candidates, stats = overlap._collect_pipeline_candidate_pairs(table_columns, memberships, options=options)
    t1 = time.time()
    overlaps, filter_stats = overlap.run_overlap_filter_pipeline(
        candidates,
        options=options,
        table_group_memberships=memberships,
        db_connect=db_connect,
        dialect=dialect,
    )
    t2 = time.time()
    stats["filter_pipeline"] = filter_stats
    pre_value_candidate_count = (
        filter_stats["value_overlap"]["input"]
        if "value_overlap" in filter_stats
        else next(reversed(filter_stats.values()))["retained"] if filter_stats else len(candidates)
    )
    return {
        "tables": len(table_columns),
        "physical_columns": len(columns),
        "stats": stats,
        "overlaps": overlaps,
        "candidates": candidates,
        "pre_value_candidate_count": pre_value_candidate_count,
        "candidate_seconds": t1 - t0,
        "value_seconds": t2 - t1,
    }


def _run_official_db(db_id: str, options: overlap.OverlapOptions) -> dict:
    database_dir = SPIDER2_SNOW_DATABASES / db_id
    if not database_dir.is_dir():
        raise RuntimeError(f"missing Spider2-Snow official database dir: {database_dir}")

    _schemas, relations, _foreign_keys = _load_official_schema(database_dir, db_id)
    columns = _official_columns(db_id, relations)
    table_columns: dict[str, list[dict]] = defaultdict(list)
    for col in columns:
        table_columns[col["table"]].append(col)

    memberships = _official_table_group_memberships(db_id, relations)
    if "value_overlap" not in {item.name for item in options.filter_pipeline}:
        t0 = time.time()
        pre_value_candidate_count, stats, filter_stats = overlap.count_pre_value_candidates(
            table_columns,
            options=options,
            table_group_memberships=memberships,
        )
        t1 = time.time()
        stats["filter_pipeline"] = filter_stats
        return {
            "tables": len(table_columns),
            "physical_columns": len(columns),
            "stats": stats,
            "overlaps": [],
            "candidates": [],
            "pre_value_candidate_count": pre_value_candidate_count,
            "candidate_seconds": t1 - t0,
            "value_seconds": 0.0,
        }

    t0 = time.time()
    candidates, stats = overlap._collect_pipeline_candidate_pairs(table_columns, memberships, options=options)
    t1 = time.time()
    needs_value_matching = "value_overlap" in {item.name for item in options.filter_pipeline}
    db_connect = (
        None
        if not needs_value_matching or options.value_match_method == "metadata_sample"
        else _snowflake_db_connect(db_id)
    )
    db_overlaps, filter_stats = overlap.run_overlap_filter_pipeline(
        candidates,
        options=options,
        table_group_memberships=memberships,
        db_connect=db_connect,
        dialect="snowflake",
    )
    t2 = time.time()
    stats["filter_pipeline"] = filter_stats
    pre_value_candidate_count = (
        filter_stats["value_overlap"]["input"]
        if "value_overlap" in filter_stats
        else next(reversed(filter_stats.values()))["retained"] if filter_stats else len(candidates)
    )
    return {
        "tables": len(table_columns),
        "physical_columns": len(columns),
        "stats": stats,
        "overlaps": db_overlaps,
        "candidates": candidates,
        "pre_value_candidate_count": pre_value_candidate_count,
        "candidate_seconds": t1 - t0,
        "value_seconds": t2 - t1,
    }


def _run_snowflake_schema_db(db_id: str, options: overlap.OverlapOptions) -> dict:
    """Run the audit against the live Snowflake schema rather than local DDL.

    Spider2 resource DDL can diverge from the hosted tables.  Value-domain
    profiles must use the same table/column identifiers that Snowflake exposes.
    """

    db_connect = _snowflake_db_connect(db_id)
    table_columns = _load_snowflake_information_schema_columns(db_id, db_connect)
    memberships = _table_group_memberships_from_columns(db_id, table_columns)
    physical_columns = sum(len(columns) for columns in table_columns.values())
    if "value_overlap" not in {item.name for item in options.filter_pipeline}:
        t0 = time.time()
        pre_value_candidate_count, stats, filter_stats = overlap.count_pre_value_candidates(
            table_columns,
            options=options,
            table_group_memberships=memberships,
        )
        t1 = time.time()
        stats["filter_pipeline"] = filter_stats
        return {
            "tables": len(table_columns),
            "physical_columns": physical_columns,
            "stats": stats,
            "overlaps": [],
            "candidates": [],
            "pre_value_candidate_count": pre_value_candidate_count,
            "candidate_seconds": t1 - t0,
            "value_seconds": 0.0,
        }

    t0 = time.time()
    candidates, stats, filter_stats, value_spec = _collect_snowflake_value_candidates_streaming(
        table_columns,
        memberships,
        options=options,
    )
    t1 = time.time()
    value_options = replace(options, filter_pipeline=(value_spec,))
    db_overlaps, value_filter_stats = overlap.run_overlap_filter_pipeline(
        candidates,
        options=value_options,
        table_group_memberships=memberships,
        db_connect=db_connect,
        dialect="snowflake",
    )
    t2 = time.time()
    filter_stats.update(value_filter_stats)
    stats["filter_pipeline"] = filter_stats
    pre_value_candidate_count = (
        filter_stats["value_overlap"]["input"]
        if "value_overlap" in filter_stats
        else next(reversed(filter_stats.values()))["retained"] if filter_stats else len(candidates)
    )
    return {
        "tables": len(table_columns),
        "physical_columns": physical_columns,
        "stats": stats,
        "overlaps": db_overlaps,
        "candidates": candidates,
        "pre_value_candidate_count": pre_value_candidate_count,
        "candidate_seconds": t1 - t0,
        "value_seconds": t2 - t1,
    }


def _collect_snowflake_value_candidates_streaming(
    table_columns: dict[str, list[dict]],
    memberships: dict[str, set[str]],
    *,
    options: overlap.OverlapOptions,
) -> tuple[list[tuple[dict, dict]], dict, dict[str, dict], object]:
    """Apply cheap stages pair-by-pair before materializing value candidates.

    The live ACS schema produces many raw domain pairs.  The normal extractor
    retains a candidate object for every raw pair so it can emit full evidence;
    the audit only needs final counts and Gold recall, so retaining rejected
    pairs wastes multiple GB without changing the pipeline's decisions.
    """

    specs = resolved_filter_pipeline(options)
    value_specs = [spec for spec in specs if spec.name == "value_overlap"]
    if len(value_specs) != 1:
        raise ValueError("Snowflake audit requires exactly one value_overlap stage")
    value_spec = value_specs[0]
    cheap_specs = [spec for spec in specs if spec.name != "value_overlap"]
    units_by_table, stats = _prepare_pipeline_comparison_units(
        table_columns,
        memberships,
        options=options,
    )
    context = FilterContext(options=options, table_group_memberships=memberships)
    filter_stats = {
        spec.name: {
            "threshold": spec.threshold,
            "metric": spec.metric,
            "input": 0,
            "retained": 0,
            "rejected": 0,
        }
        for spec in cheap_specs
    }
    retained_pairs: list[tuple[dict, dict]] = []
    raw_pairs = 0
    for left, right in _iter_pipeline_candidate_pairs(units_by_table):
        raw_pairs += 1
        candidate = OverlapCandidate(left=left, right=right)
        for spec in cheap_specs:
            stage_stats = filter_stats[spec.name]
            stage_stats["input"] += 1
            runner = FILTER_RUNNERS[spec.name]
            if not runner([candidate], spec, context):
                stage_stats["rejected"] += 1
                break
            stage_stats["retained"] += 1
        else:
            retained_pairs.append((left, right))
    stats["raw_pairs"] = raw_pairs
    stats["candidate_pairs"] = raw_pairs
    return retained_pairs, stats, filter_stats, value_spec


def _load_snowflake_information_schema_columns(
    db_id: str,
    db_connect: DbConnect,
) -> dict[str, list[dict]]:
    table_columns: dict[str, list[dict]] = defaultdict(list)
    conn = db_connect(readonly=True)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT table_schema, table_name, column_name, data_type, ordinal_position
                FROM \"{db_id.replace(chr(34), chr(34) * 2)}\".INFORMATION_SCHEMA.COLUMNS
                WHERE table_schema <> 'INFORMATION_SCHEMA'
                ORDER BY table_schema, table_name, ordinal_position
                """
            )
            for schema_name, table_name, column_name, data_type, ordinal_position in cursor:
                table_ref = f"{db_id}--{schema_name}--{table_name}"
                col_ref = f"{table_ref}--{column_name}"
                table_columns[table_ref].append({
                    "entity_name": col_ref,
                    "db_ref": db_id,
                    "table": table_ref,
                    "table_ref": table_ref,
                    "table_name": table_name,
                    "schema_name": schema_name,
                    "column": column_name,
                    "column_ref": col_ref,
                    "data_type": data_type,
                    "ordinal_position": ordinal_position,
                    "cardinality": None,
                    "min_length": None,
                    "max_length": None,
                    "avg_length": None,
                    "min_value": None,
                    "max_value": None,
                    "null_percentage": None,
                    "sample": [],
                    "topk": [],
                })
        finally:
            cursor.close()
    finally:
        conn.close()
    return table_columns


def _table_group_memberships_from_columns(
    db_id: str,
    table_columns: dict[str, list[dict]],
    *,
    min_members: int = 3,
) -> dict[str, set[str]]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for table_ref, columns in table_columns.items():
        if not columns:
            continue
        first = columns[0]
        schema_name = str(first.get("schema_name") or "")
        table_name = str(first.get("table_name") or "")
        family = db_table_group.table_family_name(table_name)
        if family == table_name.upper():
            continue
        grouped[(schema_name, family)].append(table_ref)

    memberships: dict[str, set[str]] = defaultdict(set)
    for (schema_name, family), members in grouped.items():
        if len(members) < min_members:
            continue
        group_ref = f"{db_id}--{schema_name}--table_group--{family}"
        for table_ref in members:
            memberships[table_ref].add(group_ref)
            table_name = table_ref.rsplit("--", 1)[-1]
            memberships[table_name].add(group_ref)
    return memberships


def _official_columns(db_id: str, relations: dict) -> list[dict]:
    columns: list[dict] = []
    for (schema_name, _relation_key), rel in sorted(relations.items()):
        table_name = rel.name
        table_ref = f"{db_id}--{schema_name}--{table_name}"
        for col in sorted(rel.columns.values(), key=lambda item: (item.ordinal_position, item.name)):
            col_ref = f"{table_ref}--{col.name}"
            columns.append({
                "entity_name": col_ref,
                "db_ref": db_id,
                "table": table_ref,
                "table_ref": table_ref,
                "table_name": table_name,
                "schema_name": schema_name,
                "column": col.name,
                "column_ref": col_ref,
                "data_type": col.data_type,
                "cardinality": None,
                "min_length": None,
                "max_length": None,
                "avg_length": None,
                "min_value": None,
                "max_value": None,
                "null_percentage": None,
                "sample": list(col.sample or []),
                "topk": [],
            })
    return columns


def _official_table_group_memberships(db_id: str, relations: dict, *, min_members: int = 3) -> dict[str, set[str]]:
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for (schema_name, _relation_key), rel in relations.items():
        if rel.kind != "table":
            continue
        family = db_table_group.table_family_name(rel.name)
        if family == rel.name.upper():
            continue
        grouped[(schema_name, family)].append((schema_name, rel.name))

    memberships: dict[str, set[str]] = defaultdict(set)
    for (schema_name, family), members in grouped.items():
        if len(members) < min_members:
            continue
        group_ref = f"{db_id}--{schema_name}--table_group--{family}"
        for member_schema, table_name in members:
            table_ref = f"{db_id}--{member_schema}--{table_name}"
            memberships[table_ref].add(group_ref)
            memberships[table_name].add(group_ref)
    return memberships


def _snowflake_db_connect(db_id: str) -> DbConnect:
    credential_path = SPIDER2_SNOW_CREDENTIAL
    if not credential_path.exists():
        raise FileNotFoundError(f"Snowflake credential file not found: {credential_path}")

    def connect(*args, **kwargs):
        kwargs.pop("readonly", None)
        try:
            import snowflake.connector
        except ImportError as exc:
            raise RuntimeError("snowflake-connector-python is required for Snowflake value overlap") from exc
        credentials = json.loads(credential_path.read_text(encoding="utf-8"))
        if credentials.get("username") and not credentials.get("user"):
            credentials["user"] = credentials.pop("username")
        else:
            credentials.pop("username", None)
        credentials["database"] = db_id
        return snowflake.connector.connect(*args, **credentials, **kwargs)

    return DbConnect(db_path=db_id, connect=connect, dialect="snowflake")


def _load_gold_pairs(path: Path) -> list[tuple[str, tuple[str, str]]]:
    rows: list[tuple[str, tuple[str, str]]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("pair_status") == "same_source_column":
                continue
            left = row.get("left_source") or ""
            right = row.get("right_source") or ""
            if not left or not right or left == right:
                continue
            rows.append((row.get("db_id") or "", _canonical_pair(left, right)))
    return rows


def _load_gold_sql_dbs() -> list[str]:
    return sorted({case.db_id for case in load_spider2_snow_cases(dev_only=True)})


def _canonical_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((_normalize_ref(left), _normalize_ref(right))))


def _normalize_ref(value: str) -> str:
    text = str(value or "").strip()
    if "--" in text:
        parts = text.split("--")
        if len(parts) >= 4:
            text = ".".join([parts[0], parts[1], "--".join(parts[2:-1]), parts[-1]])
    return text.lower()


def _overlap_rows(db_id: str, overlaps: list[dict]) -> list[dict]:
    rows = []
    for item in overlaps:
        stats = item.get("stats") or {}
        rows.append({
            "db_id": db_id,
            "from_ref": item.get("from_ref", ""),
            "to_ref": item.get("to_ref", ""),
            "from_table": item.get("from_table_name") or item.get("from_table", ""),
            "from_column": item.get("from_column", ""),
            "to_table": item.get("to_table_name") or item.get("to_table", ""),
            "to_column": item.get("to_column", ""),
            "overlap_coefficient": stats.get("overlap_coefficient", ""),
            "sample_hits": stats.get("sample_hits", ""),
            "sample_size": stats.get("sample_size", ""),
            "method": stats.get("method", ""),
            "estimated": stats.get("estimated", ""),
            "filter_evidence": json.dumps(item.get("filter_evidence", {}), ensure_ascii=False, sort_keys=True),
        })
    return rows


def _candidate_rows(db_id: str, candidates: list[tuple[dict, dict]]) -> list[dict]:
    rows = []
    for left, right in candidates:
        rows.append({
            "db_id": db_id,
            "from_ref": left.get("entity_name", ""),
            "to_ref": right.get("entity_name", ""),
            "from_table": left.get("table_name", ""),
            "from_column": left.get("column", ""),
            "to_table": right.get("table_name", ""),
            "to_column": right.get("column", ""),
        })
    return rows


def _recalled_gold_count_from_candidates(
    gold_pairs: set[tuple[str, str]],
    candidates: list[tuple[dict, dict]],
) -> int:
    if not gold_pairs:
        return 0
    recalled = 0
    side_sets = [(_physical_ref_set(left), _physical_ref_set(right)) for left, right in candidates]
    for left_ref, right_ref in gold_pairs:
        if any(
            (left_ref in left_set and right_ref in right_set)
            or (left_ref in right_set and right_ref in left_set)
            for left_set, right_set in side_sets
        ):
            recalled += 1
    return recalled


def _recalled_gold_count_from_overlaps(
    gold_pairs: set[tuple[str, str]],
    overlaps: list[dict],
) -> int:
    if not gold_pairs:
        return 0
    side_sets = [_overlap_physical_ref_sets(item) for item in overlaps]
    recalled = 0
    for left_ref, right_ref in gold_pairs:
        if any(
            (left_ref in left_set and right_ref in right_set)
            or (left_ref in right_set and right_ref in left_set)
            for left_set, right_set in side_sets
        ):
            recalled += 1
    return recalled


def _physical_ref_set(col: dict) -> set[str]:
    return {_normalize_ref(ref) for ref in _physical_refs(col) if ref}


def _overlap_physical_ref_sets(item: dict) -> tuple[set[str], set[str]]:
    domain_sides = item.get("domain_sides") or []
    if len(domain_sides) == 2:
        left_refs = [member.get("ref", "") for member in domain_sides[0].get("members") or []]
        right_refs = [member.get("ref", "") for member in domain_sides[1].get("members") or []]
    else:
        left_refs = [item.get("from_ref", "")]
        right_refs = [item.get("to_ref", "")]
    return (
        {_normalize_ref(ref) for ref in left_refs if ref},
        {_normalize_ref(ref) for ref in right_refs if ref},
    )


def _expanded_candidate_pair_set(candidates: list[tuple[dict, dict]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for left, right in candidates:
        for left_ref in _physical_refs(left):
            for right_ref in _physical_refs(right):
                if left_ref != right_ref:
                    pairs.add(_canonical_pair(left_ref, right_ref))
    return pairs


def _expanded_overlap_pair_set(overlaps: list[dict]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for item in overlaps:
        domain_sides = item.get("domain_sides") or []
        if len(domain_sides) == 2:
            left_refs = [member.get("ref", "") for member in domain_sides[0].get("members") or []]
            right_refs = [member.get("ref", "") for member in domain_sides[1].get("members") or []]
        else:
            left_refs = [item.get("from_ref", "")]
            right_refs = [item.get("to_ref", "")]
        for left_ref in left_refs:
            for right_ref in right_refs:
                if left_ref and right_ref and left_ref != right_ref:
                    pairs.add(_canonical_pair(left_ref, right_ref))
    return pairs


def _physical_refs(col: dict) -> list[str]:
    members = col.get("domain_members") or []
    if members:
        return [member.get("entity_name", "") for member in members if member.get("entity_name")]
    return [col.get("entity_name", "")]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _summary(
    db_rows: list[dict],
    pre_value_rows: list[dict],
    overlap_rows: list[dict],
    gold_pairs: list[tuple[str, tuple[str, str]]],
    gold_sql_dbs: list[str],
) -> dict:
    ok_rows = [row for row in db_rows if row.get("status") == "ok"]
    gold_unique = set(gold_pairs)
    total_gold = sum(int(row.get("gold_pairs") or 0) for row in ok_rows)
    total_recalled = sum(int(row.get("gold_recalled") or 0) for row in ok_rows)
    total_pre_recalled = sum(int(row.get("gold_pre_value_recalled") or 0) for row in ok_rows)
    return {
        "db_count": len(db_rows),
        "ok_db_count": len(ok_rows),
        "error_db_count": len(db_rows) - len(ok_rows),
        "total_pre_value_candidates": sum(int(row.get("pre_value_candidates") or 0) for row in ok_rows),
        "total_pre_value_candidate_rows": len(pre_value_rows),
        "total_overlap_candidates": sum(int(row.get("overlap_candidates") or 0) for row in ok_rows),
        "total_gold_pairs_in_ran_dbs": total_gold,
        "total_gold_pre_value_recalled": total_pre_recalled,
        "total_gold_pre_value_recall": round(total_pre_recalled / total_gold, 6) if total_gold else None,
        "total_gold_recalled": total_recalled,
        "total_gold_recall": round(total_recalled / total_gold, 6) if total_gold else None,
        "gold_unique_pairs_all": len(gold_unique),
        "gold_sql_db_count": len(gold_sql_dbs),
        "gold_sql_dbs_in_run": sum(1 for row in ok_rows if row.get("gold_sql_db")),
        "status_counts": dict(Counter(row.get("status") for row in db_rows)),
        "method_counts": dict(Counter(row.get("method") or "" for row in overlap_rows)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
