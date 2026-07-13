#!/usr/bin/env python3
"""Classify every official Spider2-Snow column and report domain distributions."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path


PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
if str(PONTIS_ROOT) not in sys.path:
    sys.path.insert(0, str(PONTIS_ROOT))

from extractor.spider2_snow_schema import _load_official_schema
from extractor.utils.semantic_domain import CLASSIFIER_VERSION, classify_semantic_domain


DEFAULT_ROOT = TEXT2SQL_ROOT / "data" / "Spider2" / "spider2-snow" / "resource" / "databases"
DEFAULT_OUTPUT = TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis" / "analysis" / "spider2_snow" / "column_domains"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    totals = _new_counts()
    per_database: dict[str, dict] = {}
    rows_path = args.output / "columns.csv.gz"
    fieldnames = [
        "database", "schema", "relation", "column", "data_type", "physical_family",
        "primary_role", "join_likelihood", "classification_confidence", "semantic_domains", "representation_domains",
        "entity_tokens", "blocking_keys", "official_description",
    ]

    with gzip.open(rows_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for database_dir in sorted(path for path in args.root.iterdir() if path.is_dir()):
            database = database_dir.name
            schemas, relations, _foreign_keys = _load_official_schema(database_dir, database)
            db_counts = _new_counts()
            db_signatures: set[tuple[str, str, str]] = set()
            db_unknown_signatures: set[tuple[str, str, str]] = set()
            for (schema, _), relation in sorted(relations.items()):
                for column in sorted(relation.columns.values(), key=lambda item: (item.ordinal_position, item.name)):
                    profile = classify_semantic_domain(
                        column.name,
                        column.data_type,
                        official_description=column.official_column_description,
                        sample_values=column.sample,
                    )
                    _update_counts(totals, profile, column.data_type)
                    _update_counts(db_counts, profile, column.data_type)
                    signature = (schema.upper(), column.name.upper(), column.data_type.upper())
                    db_signatures.add(signature)
                    if profile["primary_role"] == "unknown":
                        db_unknown_signatures.add(signature)
                    writer.writerow({
                        "database": database,
                        "schema": schema,
                        "relation": relation.name,
                        "column": column.name,
                        "data_type": column.data_type,
                        "physical_family": profile["physical_family"],
                        "primary_role": profile["primary_role"],
                        "join_likelihood": profile["join_likelihood"],
                        "classification_confidence": profile["classification_confidence"],
                        "semantic_domains": "|".join(profile["semantic_domains"]),
                        "representation_domains": "|".join(profile["representation_domains"]),
                        "entity_tokens": "|".join(profile["entity_tokens"]),
                        "blocking_keys": "|".join(profile["blocking_keys"]),
                        "official_description": column.official_column_description,
                    })
            per_database[database] = {
                "schema_count": len(schemas),
                "distinct_schema_name_type_signatures": len(db_signatures),
                "unknown_distinct_schema_name_type_signatures": len(db_unknown_signatures),
                **_serialise_counts(db_counts),
            }

    summary = {
        "classifier_version": CLASSIFIER_VERSION,
        "database_count": len(per_database),
        "distinct_schema_name_type_signatures": sum(
            item["distinct_schema_name_type_signatures"] for item in per_database.values()
        ),
        "unknown_distinct_schema_name_type_signatures": sum(
            item["unknown_distinct_schema_name_type_signatures"] for item in per_database.values()
        ),
        **_serialise_counts(totals),
        "per_database": per_database,
        "columns_file": str(rows_path),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "per_database"}, indent=2))
    return 0


def _new_counts() -> dict:
    return {
        "column_count": 0,
        "data_types": Counter(),
        "physical_families": Counter(),
        "primary_roles": Counter(),
        "join_likelihoods": Counter(),
        "classification_confidences": Counter(),
        "semantic_domains": Counter(),
        "representation_domains": Counter(),
    }


def _update_counts(counts: dict, profile: dict, data_type: str) -> None:
    counts["column_count"] += 1
    counts["data_types"][str(data_type or "UNKNOWN")] += 1
    counts["physical_families"][profile["physical_family"]] += 1
    counts["primary_roles"][profile["primary_role"]] += 1
    counts["join_likelihoods"][profile["join_likelihood"]] += 1
    counts["classification_confidences"][profile["classification_confidence"]] += 1
    counts["semantic_domains"].update(profile["semantic_domains"])
    counts["representation_domains"].update(profile["representation_domains"])


def _serialise_counts(counts: dict) -> dict:
    return {
        key: (dict(value.most_common()) if isinstance(value, Counter) else value)
        for key, value in counts.items()
    }


def _markdown(summary: dict) -> str:
    lines = [
        "# Spider2-Snow Column Domain Classification",
        "",
        f"- Classifier version: {summary['classifier_version']}",
        f"- Databases: {summary['database_count']}",
        f"- Columns: {summary['column_count']:,}",
        f"- Distinct database/schema/name/type signatures: {summary['distinct_schema_name_type_signatures']:,}",
        f"- Unknown distinct signatures: {summary['unknown_distinct_schema_name_type_signatures']:,}",
        "",
    ]
    for title, key in (
        ("Primary roles", "primary_roles"),
        ("Join likelihood", "join_likelihoods"),
        ("Classification confidence", "classification_confidences"),
        ("Semantic domains", "semantic_domains"),
        ("Physical families", "physical_families"),
        ("Representation domains", "representation_domains"),
    ):
        lines.extend([f"## {title}", "", "| Label | Columns |", "|---|---:|"])
        lines.extend(f"| `{label}` | {count:,} |" for label, count in summary[key].items())
        lines.append("")
    lines.extend([
        "## Per database", "",
        "| Database | Schemas | Columns | Distinct signatures | Unknown signatures | High | Medium | Low | Unknown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for database, counts in sorted(summary["per_database"].items()):
        likelihood = counts["join_likelihoods"]
        lines.append(
            f"| `{database}` | {counts['schema_count']:,} | {counts['column_count']:,} | "
            f"{counts['distinct_schema_name_type_signatures']:,} | {counts['unknown_distinct_schema_name_type_signatures']:,} | "
            f"{likelihood.get('high', 0):,} | {likelihood.get('medium', 0):,} | "
            f"{likelihood.get('low', 0):,} | {likelihood.get('unknown', 0):,} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
