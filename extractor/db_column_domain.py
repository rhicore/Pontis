"""Unified configurable extractor for column-domain candidates."""
from __future__ import annotations

import logging
from collections import Counter

from extractor.utils.column_domain_entities import sync_column_domains
from extractor.utils.database_catalog import iter_database_contexts
from storage.workspace import Workspace


logger = logging.getLogger(__name__)
PAIRWISE_FILTER = "pairwise_filter"
ONLINE_CLUSTERING = "online_clustering"
STRATEGIES = {PAIRWISE_FILTER, ONLINE_CLUSTERING}


def generate(
    workspace: Workspace,
    config=None,
    *,
    strategy: str,
    strategy_options: dict | None = None,
) -> dict[str, int]:
    """Generate unified ``column_domain:domain`` entities with one strategy."""

    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {sorted(STRATEGIES)}, got {strategy!r}")
    options = dict(strategy_options or {})
    totals = Counter()
    logger.info("=== DB column domain extraction: %s ===", strategy)

    for database in iter_database_contexts(workspace):
        if strategy == PAIRWISE_FILTER:
            from extractor.db_column_overlap import build_pairwise_candidates_for_database
            from extractor.utils.overlap_options import _resolve_options

            candidates = build_pairwise_candidates_for_database(
                database.ref,
                database.node,
                database.connect,
                workspace,
                _resolve_options(config, **options),
            ) or []
            normalized = [_pairwise_candidate(candidate, options) for candidate in candidates]
            totals["candidate_groups"] += len(normalized)
        else:
            from extractor.db_value_domain import build_online_candidates_for_database
            from extractor.utils.online_value_domains import OnlineValueDomainConfig
            from extractor.utils.overlap_options import _resolve_options

            summaries, stats = build_online_candidates_for_database(
                workspace,
                db_ref=database.ref,
                db_node=database.node,
                db_connect=database.connect,
                domain_config=OnlineValueDomainConfig(
                    overlap_threshold=float(options.get("overlap_threshold", 0.5)),
                    match_policy="union_and_anchor",
                    anchor_overlap_threshold=options.get("anchor_overlap_threshold"),
                    min_anchor_support=float(options.get("min_anchor_support", 0.75)),
                    max_anchors=int(options.get("max_anchors", 8)),
                ),
                min_members=max(2, int(options.get("min_members", 2))),
                max_logical_members=(
                    int(options["max_logical_members"])
                    if options.get("max_logical_members") is not None
                    else None
                ),
                value_read_method=str(options.get("value_read_method", "exact_distinct")),
                value_read_options=_resolve_options(
                    config,
                    **dict(options.get("value_read_options") or {}),
                ),
            )
            totals.update({key: value for key, value in stats.items() if key != "databases"})
            normalized = [_online_candidate(summary) for summary in summaries]
            if options.get("max_logical_members") is not None:
                for candidate in normalized:
                    candidate["metadata"]["max_logical_members"] = int(options["max_logical_members"])

        totals["created"] += sync_column_domains(workspace, database.ref, normalized)
        totals["column_domains"] += len(normalized)
        totals["databases"] += 1

    logger.info("  Column domain totals: %s", dict(totals))
    return dict(totals)


def _pairwise_candidate(candidate: dict, options: dict) -> dict:
    stats = dict(candidate.get("stats", {}))
    stats.pop("column_count", None)
    stats.pop("pair_count", None)
    if "columns" in candidate:
        member_refs = [column.get("ref") for column in candidate["columns"]]
        evidence = {
            "pair_stats": candidate.get("pair_stats", []),
            "domain_sides": candidate.get("domain_sides", []),
            "group_policy_evidence": candidate.get("group_policy_evidence", {}),
        }
    else:
        member_refs = [candidate.get("from_ref"), candidate.get("to_ref")]
        evidence = {
            "filter_evidence": candidate.get("filter_evidence", {}),
            "filter_pipeline": candidate.get("filter_pipeline", []),
        }
    return {
        "member_refs": member_refs,
        "metadata": {
            "extraction_strategy": PAIRWISE_FILTER,
            "value_match_method": options.get("value_match_method", "sql"),
            "sources": candidate.get("sources", []),
            "stats": stats,
            "review_status": candidate.get("review_status") or "pending_review",
            **evidence,
        },
    }


def _online_candidate(summary: dict) -> dict:
    summary = dict(summary)
    member_refs = summary.pop("member_refs")
    for internal in ("_ref", "name", "db_ref", "schema_ref"):
        summary.pop(internal, None)
    summary.pop("member_count", None)
    summary["extraction_strategy"] = ONLINE_CLUSTERING
    return {"member_refs": member_refs, "metadata": summary}
