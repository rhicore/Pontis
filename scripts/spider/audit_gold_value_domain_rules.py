#!/usr/bin/env python3
"""Replay value-domain admission rules over exact Golden join metrics."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
if str(PONTIS_ROOT) not in sys.path:
    sys.path.insert(0, str(PONTIS_ROOT))

from extractor.db_value_domain import (
    _GENERIC_DOMAIN_TOKENS,
    _KEY_LIKE,
    _profiles_strong_semantic_match,
    _semantic_hard_conflict,
    _semantic_profile,
)


DEFAULT_INPUT = (
    TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis" / "analysis"
    / "spider2_snow" / "gold_join_value_collision_stats.csv"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    audited = [_audit(row) for row in rows]
    missed = [row for row in audited if not row["direct_admission"]]
    summary = {
        "gold_pairs": len(audited),
        "direct_admitted": len(audited) - len(missed),
        "direct_recall": round((len(audited) - len(missed)) / len(audited), 6) if audited else None,
        "missed": len(missed),
        "miss_reasons": dict(Counter(reason for row in missed for reason in row["domain_miss_reasons"])),
        "missed_pairs": missed,
    }
    text = json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def _audit(row: dict) -> dict:
    left = _metadata(row["left_source"], row["left_data_type"])
    right = _metadata(row["right_source"], row["right_data_type"])
    left_profile = _semantic_profile(left)
    right_profile = _semantic_profile(right)
    left_tokens = set(left_profile.get("entity_tokens") or []) - _GENERIC_DOMAIN_TOKENS
    right_tokens = set(right_profile.get("entity_tokens") or []) - _GENERIC_DOMAIN_TOKENS
    shared_tokens = left_tokens & right_tokens
    same_role = _normalise(left["column"]) == _normalise(right["column"])
    cross_schema = left["schema_name"].upper() != right["schema_name"].upper()
    strong_semantics = _profiles_strong_semantic_match(left_profile, right_profile)
    coverage = float(row["coverage_min"])
    jaccard = float(row["jaccard"])
    left_role = str(left_profile["primary_role"])
    right_role = str(right_profile["primary_role"])
    roles = {left_role, right_role}
    reasons: list[str] = []

    if _semantic_hard_conflict(left_profile, right_profile) and not (shared_tokens and cross_schema):
        reasons.append("semantic_veto")
    if cross_schema and not shared_tokens and jaccard < 0.9:
        reasons.append("cross_schema_weak")
    if not strong_semantics and not same_role:
        if left_role in _KEY_LIKE and right_role in _KEY_LIKE and jaccard < 0.05:
            reasons.append("weak_key_jaccard")
        elif roles & _KEY_LIKE and roles & {"unknown", "categorical", "name"} and jaccard < 0.5:
            reasons.append("weak_alias_jaccard")

    if strong_semantics or same_role or (shared_tokens and cross_schema):
        threshold = 0.0
    elif left_role in _KEY_LIKE and right_role in _KEY_LIKE:
        threshold = 0.3
    else:
        threshold = 0.5
    if coverage < threshold:
        reasons.append("value_below_dynamic_threshold")

    return {
        "db_id": row["db_id"],
        "left_source": row["left_source"],
        "right_source": row["right_source"],
        "coverage_min": coverage,
        "jaccard": jaccard,
        "left_role": left_role,
        "right_role": right_role,
        "shared_entity_tokens": sorted(shared_tokens),
        "required_overlap": threshold,
        "direct_admission": not reasons,
        "domain_miss_reasons": reasons,
    }


def _metadata(source: str, data_type: str) -> dict:
    parts = source.split(".")
    return {
        "column": parts[-1],
        "table_name": parts[-2],
        "schema_name": parts[-3],
        "data_type": data_type,
        "sample": [],
        "domain_profile": {},
    }


def _normalise(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


if __name__ == "__main__":
    raise SystemExit(main())
