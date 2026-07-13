#!/usr/bin/env python3
"""Evaluate online value-domain clustering from cached distinct hash indexes."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from array import array
from pathlib import Path


PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
if str(PONTIS_ROOT) not in sys.path:
    sys.path.insert(0, str(PONTIS_ROOT))

from extractor.utils.domain_profile import physical_family
from extractor.utils.online_value_domains import (
    OnlineValueDomainConfig,
    ValueColumn,
    build_online_value_domains,
)
from extractor.utils.semantic_domain import classify_semantic_domain
from extractor.db_value_domain import (
    _domain_is_compatible,
    _minimum_domain_overlap,
    _ordered_value_columns,
    _semantic_profile,
)


DEFAULT_INDEX_ROOT = PONTIS_ROOT / "workspace" / "baselines" / "pontis" / "value_index"
DEFAULT_GOLD = (
    TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis" / "analysis" / "spider2_snow"
    / "gold_value_overlap_lineage_current" / "gold_value_overlap_pairs.csv"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", nargs="?", default="AIRLINES")
    parser.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--policy", choices=("union", "union_and_anchor"), default="union_and_anchor")
    parser.add_argument("--min-anchor-support", type=float, default=0.5)
    parser.add_argument("--max-anchors", type=int, default=8)
    parser.add_argument("--bucket", choices=("physical", "semantic", "production"), default="production")
    parser.add_argument("--order", choices=("ref", "cardinality_desc", "cardinality_asc"), default="cardinality_desc")
    args = parser.parse_args()

    columns = _load_columns(args.index_root / args.database, bucket_mode=args.bucket)
    columns = _ordered_value_columns(columns) if args.bucket == "production" else _ordered(columns, args.order)
    result = build_online_value_domains(
        columns,
        OnlineValueDomainConfig(
            overlap_threshold=args.threshold,
            match_policy=args.policy,
            min_anchor_support=args.min_anchor_support,
            max_anchors=args.max_anchors,
        ),
        compatible=_domain_is_compatible if args.bucket == "production" else None,
        minimum_overlap=_minimum_domain_overlap if args.bucket == "production" else None,
    )
    gold = _gold_pairs(args.gold, args.database, {column.ref for column in columns})
    covered = [pair for pair in gold if _co_clustered(pair, result.assignments)]
    pairwise = len(columns) * (len(columns) - 1) // 2
    output = {
        "database": args.database,
        "policy": args.policy,
        "threshold": args.threshold,
        "order": args.order,
        "bucket_mode": args.bucket,
        "indexed_columns": len(columns),
        "value_domains": len(result.domains),
        "multi_column_domains": sum(len(domain.members) > 1 for domain in result.domains),
        "largest_domain": max((len(domain.members) for domain in result.domains), default=0),
        "pairwise_column_comparisons": pairwise,
        "domain_comparisons": result.domain_comparisons,
        "anchor_comparisons": result.anchor_comparisons,
        "comparison_reduction": round(1 - result.domain_comparisons / pairwise, 6) if pairwise else 0.0,
        "gold_pairs_with_both_indexes": len(gold),
        "gold_pairs_co_clustered": len(covered),
        "gold_recall": round(len(covered) / len(gold), 6) if gold else None,
        "domains": [
            {
                "domain_id": domain.domain_id,
                "bucket": domain.bucket,
                "member_count": len(domain.members),
                "union_cardinality": len(domain.union_values),
                "members": [column.ref for column in domain.members],
            }
            for domain in result.domains
        ],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def _load_columns(database_dir: Path, *, bucket_mode: str) -> list[ValueColumn]:
    columns: list[ValueColumn] = []
    for metadata_path in sorted(database_dir.glob("*.json")):
        data_path = metadata_path.with_suffix(".u64")
        if not data_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        values = array("Q")
        with data_path.open("rb") as handle:
            values.fromfile(handle, data_path.stat().st_size // values.itemsize)
        if not values:
            continue
        metadata["semantic_profile"] = _semantic_profile(metadata)
        columns.append(ValueColumn(
            ref=str(metadata["entity_name"]),
            values=frozenset(values),
            bucket=_column_bucket(metadata, bucket_mode),
            metadata=metadata,
        ))
    return columns


def _column_bucket(metadata: dict, mode: str) -> tuple[str, ...]:
    schema = str(metadata.get("schema_name") or "").upper()
    family = physical_family(metadata.get("data_type"))
    if mode == "production":
        return (schema,)
    if mode == "physical":
        return schema, family
    profile = classify_semantic_domain(
        str(metadata.get("column") or ""),
        metadata.get("data_type"),
    )
    role = str(profile["primary_role"])
    if role == "unknown":
        return schema, role, family
    if role == "measure":
        semantic = tuple(label for label in profile["semantic_domains"] if label != "unclassified")
        return (schema, role, *semantic)
    return schema, role


def _ordered(columns: list[ValueColumn], order: str) -> list[ValueColumn]:
    if order == "cardinality_desc":
        return sorted(columns, key=lambda column: (-len(column.values), column.ref))
    if order == "cardinality_asc":
        return sorted(columns, key=lambda column: (len(column.values), column.ref))
    return sorted(columns, key=lambda column: column.ref)


def _gold_pairs(path: Path, database: str, indexed: set[str]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("db_id") != database:
                continue
            left = str(row.get("left_source") or "").replace(".", "--")
            right = str(row.get("right_source") or "").replace(".", "--")
            if left in indexed and right in indexed and left != right:
                pairs.add(tuple(sorted((left, right))))
    return sorted(pairs)


def _co_clustered(pair: tuple[str, str], assignments: dict[str, int]) -> bool:
    left, right = pair
    return left in assignments and right in assignments and assignments[left] == assignments[right]


if __name__ == "__main__":
    raise SystemExit(main())
