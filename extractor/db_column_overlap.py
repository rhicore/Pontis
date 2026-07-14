"""Pairwise strategy for the unified column-domain extractor.

This module discovers and groups pairwise evidence only. Graph persistence and
pipeline registration belong to :mod:`extractor.db_column_domain`.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from extractor.utils.database_catalog import load_database_columns, load_table_group_memberships
from extractor.utils.overlap_candidates import (
    _collect_pipeline_candidate_pairs,
    _prepare_pipeline_comparison_units,
)
from extractor.utils.overlap_evidence import (
    _collapse_same_table_group_columns,
    _detect_name_overlaps,
    _group_pair_overlaps,
    _merge_overlap_evidence,
)
from extractor.utils.overlap_options import OverlapOptions, _resolve_options
from extractor.utils.overlap_filter_pipeline import run_overlap_filter_pipeline
from extractor.utils.overlap_group_policy import apply_overlap_group_policy
from storage.workspace import Workspace

logger = logging.getLogger(__name__)


def build_pairwise_candidates_for_database(
    db_ref: str,
    db_node: dict,
    db_connect,
    workspace: Workspace,
    options: OverlapOptions,
) -> list[dict] | None:
    """Build retained pair/group candidates without deciding their graph type."""

    if not callable(db_connect):
        logger.info("  Skipping %s: no storage db_connect handle", db_ref)
        return None
    dialect = str(getattr(db_connect, "dialect", "") or db_node.get("dialect") or "sqlite").lower()

    columns_info = load_database_columns(workspace, db_ref)
    if not columns_info:
        return None

    if len(columns_info) < 2:
        logger.info(f"  Skipping {db_ref}: only {len(columns_info)} columns")
        return None

    table_columns = defaultdict(list)
    for col in columns_info:
        table_columns[col['table']].append(col)
    table_group_memberships = load_table_group_memberships(
        workspace,
        table_names=table_columns.keys(),
        table_refs=(col.get("table_ref") for col in columns_info),
    )

    candidate_pairs, candidate_stats = _collect_pipeline_candidate_pairs(
        table_columns,
        table_group_memberships,
        options=options,
    )
    pair_overlaps, filter_stats = run_overlap_filter_pipeline(
        candidate_pairs,
        options=options,
        table_group_memberships=table_group_memberships,
        db_connect=db_connect,
        dialect=dialect,
    )
    logger.info(
        "  Overlap filter pipeline for %s: seed=%s; stages=%s",
        db_ref,
        candidate_stats["candidate_pairs"],
        filter_stats,
    )

    # Logical column domains are first-class columns from this point onward.
    # Group them exactly like physical columns; expand physical members only
    # when graph edges are written.
    value_overlaps = [
        overlap
        for overlap in (
            (
                overlap
                if overlap.get("domain_sides")
                else _collapse_same_table_group_columns(overlap, table_group_memberships)
            )
            for overlap in _group_pair_overlaps(pair_overlaps)
        )
        if overlap is not None
    ]
    if options.name_overlap_enabled:
        logical_units, _logical_stats = _prepare_pipeline_comparison_units(
            table_columns,
            table_group_memberships,
            options=options,
        )
        logical_columns = {
            column["entity_name"]: column
            for columns in logical_units.values()
            for column in columns
        }
        name_overlaps = _detect_name_overlaps(list(logical_columns.values()), table_group_memberships)
    else:
        name_overlaps = []
    overlap_groups = _merge_overlap_evidence(value_overlaps + name_overlaps)
    overlap_groups, group_policy_stats = apply_overlap_group_policy(overlap_groups, options)
    logger.info("  Overlap group policy for %s: %s", db_ref, group_policy_stats)
    return overlap_groups


# Compatibility aliases used by existing audit scripts.
_load_db_columns = load_database_columns
_load_table_group_memberships = load_table_group_memberships


def _group_overlap_table_refs(columns: list[dict], domain_sides: list[dict]) -> list[str]:
    """Return physical tables represented by a grouped pairwise candidate."""

    domain_refs = {str(side.get("domain_ref") or "") for side in domain_sides}
    table_refs = {
        str(member.get("table_ref") or member.get("table") or "")
        for side in domain_sides
        for member in side.get("members") or []
    }
    table_refs.update(
        str(column.get("table_ref") or column.get("table") or "")
        for column in columns
        if str(column.get("ref") or "") not in domain_refs
    )
    return sorted(ref for ref in table_refs if ref)
