"""Static post-group policy for overlap entities and AI review routing."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict

from extractor.utils.overlap_candidates import _tokens
from extractor.utils.overlap_options import OverlapOptions
from extractor.utils.semantic_domain import classify_semantic_domain


_GENERIC_ENTITY_TOKENS = {
    "id", "identifier", "key", "code", "name", "number", "num", "date",
    "type", "value", "column", "field",
}
_KEY_TOKENS = {"id", "identifier", "uuid", "guid", "key", "code", "barcode"}
_LOCAL_ORDINAL_TOKENS = {"sequence", "ordinal", "position", "rownum", "row_number"}
_TEXT_PAYLOAD_TOKENS = {"title", "description", "comment", "summary", "caption", "text"}


def apply_overlap_group_policy(
    overlaps: list[Dict],
    options: OverlapOptions,
) -> tuple[list[Dict], dict[str, int]]:
    """Drop statically useless groups and label the remaining review route."""

    stats = Counter(input=len(overlaps))
    if not options.group_policy_enabled:
        return overlaps, dict(stats | Counter(retained=len(overlaps)))

    retained: list[Dict] = []
    for overlap in overlaps:
        disposition, evidence = classify_overlap_group(overlap, options)
        stats[disposition] += 1
        if disposition.startswith("rejected_"):
            continue
        updated = dict(overlap)
        updated["review_status"] = disposition
        updated["group_policy_evidence"] = evidence
        retained.append(updated)
    stats["retained"] = len(retained)
    return retained, dict(stats)


def classify_overlap_group(overlap: Dict, options: OverlapOptions) -> tuple[str, dict[str, Any]]:
    sources = set(overlap.get("sources") or [])
    columns = list(overlap.get("columns") or [])
    min_overlap = _group_min_overlap(overlap)
    token_sets = [_tokens(str(column.get("column") or "")) for column in columns]

    if options.group_drop_name_only and "value_domain" not in sources:
        return "rejected_name_only", {"sources": sorted(sources)}

    if options.group_drop_local_ordinal and token_sets and all(
        bool(tokens & _LOCAL_ORDINAL_TOKENS) and not bool(tokens & _KEY_TOKENS)
        for tokens in token_sets
    ):
        return "rejected_local_ordinal", {
            "tokens": sorted(set().union(*token_sets)),
            "min_overlap_coefficient": min_overlap,
        }

    semantic_profiles = [
        classify_semantic_domain(str(column.get("column") or ""), str(column.get("type") or ""))
        for column in columns
    ]
    if (
        options.group_drop_low_overlap_text
        and min_overlap < options.group_low_overlap_text_threshold
        and token_sets
        and all(bool(tokens & _TEXT_PAYLOAD_TOKENS) for tokens in token_sets)
        and all(profile["join_likelihood"] == "low" for profile in semantic_profiles)
    ):
        return "rejected_low_overlap_text", {
            "tokens": sorted(set().union(*token_sets)),
            "min_overlap_coefficient": min_overlap,
            "threshold": options.group_low_overlap_text_threshold,
        }

    common_tokens = set.intersection(*token_sets) if token_sets else set()
    specific_common = common_tokens - _GENERIC_ENTITY_TOKENS
    entity_frequency = Counter(
        token
        for tokens in token_sets
        for token in tokens - _GENERIC_ENTITY_TOKENS - _LOCAL_ORDINAL_TOKENS
    )
    majority_ratio = (
        entity_frequency.most_common(1)[0][1] / len(columns)
        if columns and entity_frequency
        else 0.0
    )
    key_like = any(tokens & _KEY_TOKENS for tokens in token_sets)
    auto_accept = (
        bool(specific_common) and min_overlap >= options.group_auto_accept_min_overlap
    ) or (
        key_like and majority_ratio >= 0.8 and min_overlap >= options.adaptive_sample_min_overlap
    )
    evidence = {
        "min_overlap_coefficient": min_overlap,
        "specific_common_tokens": sorted(specific_common),
        "majority_entity_ratio": round(majority_ratio, 6),
        "key_like": key_like,
        "semantic_roles": sorted({profile["primary_role"] for profile in semantic_profiles}),
    }
    return ("auto_accept" if auto_accept else "ai_review"), evidence


def _group_min_overlap(overlap: Dict) -> float:
    stats = overlap.get("stats") or {}
    return float(stats.get("min_overlap_coefficient", stats.get("overlap_coefficient", 0.0)) or 0.0)
