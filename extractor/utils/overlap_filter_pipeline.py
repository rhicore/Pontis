"""Ordered, thresholded filters for column-overlap candidates.

Compaction decides the comparison units before this module runs. Every
subsequent decision is a peer filter stage: it receives the candidates retained
by the previous stage, writes its evidence to each retained candidate, and
passes only scores at or above its configured threshold onward.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable, Dict

from extractor.utils.overlap_candidates import (
    _column_shape_profile,
    _id_family,
    _is_same_table_overlap_representative,
    _key_like_candidate,
    _length_ranges_overlap,
    _name_token_overlap_candidate,
    _repeated_key_names,
    _shape_compatible as _shape_values_compatible,
    _shape_incompatible_reason,
    _prepare_pipeline_comparison_units,
    _iter_pipeline_candidate_pairs,
    _table_group_representative_tables,
    _tokens,
    _value_shape_class,
)
from extractor.utils.domain_profile import domain_compatibility
from extractor.utils.overlap_options import OverlapFilterSpec, OverlapOptions
from extractor.utils.overlap_value_matchers import _detect_column_overlaps


@dataclass
class OverlapCandidate:
    """One pair moving through the overlap filter pipeline."""

    left: Dict[str, Any]
    right: Dict[str, Any]
    filter_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    value_overlap: dict[str, Any] | None = None

    @property
    def key(self) -> frozenset[str]:
        return frozenset((str(self.left["entity_name"]), str(self.right["entity_name"])))


@dataclass(frozen=True)
class FilterContext:
    options: OverlapOptions
    table_group_memberships: dict[str, set[str]]
    db_connect: Any = None
    dialect: str = "sqlite"


FilterRunner = Callable[[list[OverlapCandidate], OverlapFilterSpec, FilterContext], list[OverlapCandidate]]


def run_overlap_filter_pipeline(
    pairs: list[tuple[Dict, Dict]],
    *,
    options: OverlapOptions,
    table_group_memberships: dict[str, set[str]],
    db_connect=None,
    dialect: str = "sqlite",
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Run the configured filters and return payloads for surviving pairs."""

    context = FilterContext(
        options=options,
        table_group_memberships=table_group_memberships,
        db_connect=db_connect,
        dialect=dialect,
    )
    candidates = [OverlapCandidate(left=left, right=right) for left, right in pairs]
    stats: dict[str, dict[str, Any]] = {}
    for spec in resolved_filter_pipeline(options):
        runner = FILTER_RUNNERS.get(spec.name)
        if runner is None:
            raise ValueError(f"Unknown overlap filter stage: {spec.name}")
        input_count = len(candidates)
        candidates = runner(candidates, spec, context)
        stats[spec.name] = {
            "threshold": spec.threshold,
            "metric": spec.metric,
            "input": input_count,
            "retained": len(candidates),
            "rejected": input_count - len(candidates),
        }

    return [_overlap_payload(candidate) for candidate in candidates if candidate.value_overlap], stats


def count_pre_value_candidates(
    table_columns: dict[str, list[Dict]],
    *,
    options: OverlapOptions,
    table_group_memberships: dict[str, set[str]],
) -> tuple[int, dict, dict[str, dict[str, Any]]]:
    """Count candidates entering value matching without materializing all pairs.

    This is intentionally streaming.  Wide databases can have tens of millions
    of raw logical-column pairs, while the caller only needs the number that
    survives the cheap stages.
    """

    specs = resolved_filter_pipeline(options)
    if any(spec.name == "value_overlap" for spec in specs):
        raise ValueError("count_pre_value_candidates requires a pipeline without value_overlap")

    units_by_table, candidate_stats = _prepare_pipeline_comparison_units(
        table_columns,
        table_group_memberships,
        options=options,
    )
    context = FilterContext(options=options, table_group_memberships=table_group_memberships)
    filter_stats = {
        spec.name: {
            "threshold": spec.threshold,
            "metric": spec.metric,
            "input": 0,
            "retained": 0,
            "rejected": 0,
        }
        for spec in specs
    }

    stage_names = tuple(spec.name for spec in specs)
    supported_fast_stages = {
        "same_schema",
        "different_table_group",
        "name_token_overlap",
        "domain_compatible",
        "shape_compatible",
    }
    if set(stage_names).issubset(supported_fast_stages):
        return _count_common_cheap_pipeline(
            units_by_table,
            candidate_stats,
            specs,
            table_group_memberships,
            filter_stats,
        )

    retained_count = 0
    raw_count = 0
    for left, right in _iter_pipeline_candidate_pairs(units_by_table):
        raw_count += 1
        candidate = OverlapCandidate(left=left, right=right)
        kept = True
        for spec in specs:
            stage_stats = filter_stats[spec.name]
            stage_stats["input"] += 1
            runner = FILTER_RUNNERS.get(spec.name)
            if runner is None:
                raise ValueError(f"Unknown overlap filter stage: {spec.name}")
            if not runner([candidate], spec, context):
                stage_stats["rejected"] += 1
                kept = False
                break
            stage_stats["retained"] += 1
        if kept:
            retained_count += 1

    candidate_stats["raw_pairs"] = raw_count
    candidate_stats["candidate_pairs"] = raw_count
    return retained_count, candidate_stats, filter_stats


def _count_common_cheap_pipeline(
    units_by_table: dict[str, list[Dict]],
    candidate_stats: dict,
    specs: tuple[OverlapFilterSpec, ...],
    table_group_memberships: dict[str, set[str]],
    filter_stats: dict[str, dict[str, Any]],
) -> tuple[int, dict, dict[str, dict[str, Any]]]:
    """Count the Spider cheap pipeline without allocating candidate objects."""

    units = [column for columns in units_by_table.values() for column in columns]
    tokens_by_ref = {
        column["entity_name"]: _tokens(column.get("column", ""))
        for column in units
    }
    groups_by_table = {
        table: table_group_memberships.get(str(table), set())
        for table in units_by_table
    }
    retained_count = 0
    raw_count = 0
    table_refs = sorted(units_by_table)

    def passes_stage(spec: OverlapFilterSpec, left: Dict, right: Dict) -> bool:
        if spec.name == "same_schema":
            left_schema = str(left.get("schema_name") or "")
            right_schema = str(right.get("schema_name") or "")
            return (1.0 if not left_schema or not right_schema or left_schema == right_schema else 0.0) >= spec.threshold
        if spec.name == "different_table_group":
            left_groups = groups_by_table.get(left.get("table"), set())
            right_groups = groups_by_table.get(right.get("table"), set())
            return (1.0 if not (left_groups & right_groups) else 0.0) >= spec.threshold
        if spec.name == "name_token_overlap":
            return len(tokens_by_ref[left["entity_name"]] & tokens_by_ref[right["entity_name"]]) >= spec.threshold
        if spec.name == "domain_compatible":
            compatible, _reason, _evidence = domain_compatibility(left, right)
            return (1.0 if compatible else 0.0) >= spec.threshold
        if spec.name == "shape_compatible":
            return (1.0 if _length_ranges_overlap(left, right) else 0.0) >= spec.threshold
        raise ValueError(f"Unsupported fast overlap filter stage: {spec.name}")

    def inspect(left: Dict, right: Dict) -> None:
        nonlocal raw_count, retained_count
        raw_count += 1
        for spec in specs:
            stage_stats = filter_stats[spec.name]
            stage_stats["input"] += 1
            if not passes_stage(spec, left, right):
                stage_stats["rejected"] += 1
                return
            stage_stats["retained"] += 1
        retained_count += 1

    for index, table1 in enumerate(table_refs):
        for table2 in table_refs[index + 1:]:
            for left in units_by_table[table1]:
                for right in units_by_table[table2]:
                    inspect(left, right)
    for columns in units_by_table.values():
        for left, right in combinations(columns, 2):
            inspect(left, right)

    candidate_stats["raw_pairs"] = raw_count
    candidate_stats["candidate_pairs"] = raw_count
    return retained_count, candidate_stats, filter_stats


def resolved_filter_pipeline(options: OverlapOptions) -> tuple[OverlapFilterSpec, ...]:
    """Use explicit config when present, otherwise preserve the legacy switches."""

    if options.filter_pipeline:
        return options.filter_pipeline

    specs: list[OverlapFilterSpec] = []
    if options.same_schema_only:
        specs.append(OverlapFilterSpec("same_schema"))
    if options.skip_same_table_group:
        specs.append(OverlapFilterSpec("different_table_group"))
    if not options.same_table_overlap_enabled:
        specs.append(OverlapFilterSpec("different_table"))
    if options.same_table_group_representative_only:
        specs.append(OverlapFilterSpec("table_group_representative"))
    if options.require_name_token_overlap:
        specs.append(OverlapFilterSpec("name_token_overlap"))
    if options.domain_filter_enabled:
        specs.append(OverlapFilterSpec("domain_compatible"))
    if options.key_like_only:
        specs.append(OverlapFilterSpec("key_like"))
    if options.require_repeated_key_name:
        specs.append(OverlapFilterSpec("repeated_key_name"))
    if options.shape_filter_enabled:
        specs.append(OverlapFilterSpec("shape_compatible"))
    if options.value_overlap_enabled:
        specs.append(OverlapFilterSpec("value_overlap", threshold=0.0, metric="overlap_coefficient"))
    return tuple(specs)


def _record(candidate: OverlapCandidate, spec: OverlapFilterSpec, score: float, **evidence: Any) -> None:
    candidate.filter_evidence[spec.name] = {
        "score": score,
        "threshold": spec.threshold,
        "metric": spec.metric,
        **evidence,
    }


def _scalar_filter(
    candidates: list[OverlapCandidate],
    spec: OverlapFilterSpec,
    scorer: Callable[[OverlapCandidate], tuple[float, dict[str, Any]]],
) -> list[OverlapCandidate]:
    retained: list[OverlapCandidate] = []
    for candidate in candidates:
        score, evidence = scorer(candidate)
        if score < spec.threshold:
            continue
        _record(candidate, spec, score, **evidence)
        retained.append(candidate)
    return retained


def _same_schema(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    def score(candidate: OverlapCandidate):
        left = str(candidate.left.get("schema_name") or "")
        right = str(candidate.right.get("schema_name") or "")
        return (1.0 if not left or not right or left == right else 0.0, {"left_schema": left, "right_schema": right})

    return _scalar_filter(candidates, spec, score)


def _different_table(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    return _scalar_filter(
        candidates,
        spec,
        lambda candidate: (
            1.0 if candidate.left.get("table") != candidate.right.get("table") else 0.0,
            {"left_table": candidate.left.get("table"), "right_table": candidate.right.get("table")},
        ),
    )


def _different_table_group(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, context: FilterContext) -> list[OverlapCandidate]:
    memberships = context.table_group_memberships

    def score(candidate: OverlapCandidate):
        left_groups = sorted(memberships.get(str(candidate.left.get("table")), set()))
        right_groups = sorted(memberships.get(str(candidate.right.get("table")), set()))
        shared = sorted(set(left_groups) & set(right_groups))
        return (1.0 if not shared else 0.0, {"left_groups": left_groups, "right_groups": right_groups, "shared_groups": shared})

    return _scalar_filter(candidates, spec, score)


def _table_group_representative(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, context: FilterContext) -> list[OverlapCandidate]:
    representatives = _table_group_representative_tables(
        (candidate.left.get("table") for candidate in candidates),
        context.table_group_memberships,
    )
    representatives.update(_table_group_representative_tables(
        (candidate.right.get("table") for candidate in candidates),
        context.table_group_memberships,
    ))

    def score(candidate: OverlapCandidate):
        if candidate.left.get("table") != candidate.right.get("table"):
            return 1.0, {"applies": False}
        table = str(candidate.left.get("table") or "")
        kept = _is_same_table_overlap_representative(table, representatives, context.table_group_memberships)
        return (1.0 if kept else 0.0, {"applies": True, "table": table, "representatives": representatives})

    return _scalar_filter(candidates, spec, score)


def _name_token_overlap(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    def score(candidate: OverlapCandidate):
        shared = sorted(_tokens(candidate.left.get("column", "")) & _tokens(candidate.right.get("column", "")))
        return float(len(shared)), {"tokens": shared}

    return _scalar_filter(candidates, spec, score)


def _domain_compatible(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    def score(candidate: OverlapCandidate):
        compatible, reason, evidence = domain_compatibility(candidate.left, candidate.right)
        return (1.0 if compatible else 0.0, {"reason": reason, **evidence})

    return _scalar_filter(candidates, spec, score)


def _key_like(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    def score(candidate: OverlapCandidate):
        return (1.0 if _key_like_candidate(candidate.left, candidate.right) else 0.0, {"left_id_family": _id_family(candidate.left.get("column", "")), "right_id_family": _id_family(candidate.right.get("column", ""))})

    return _scalar_filter(candidates, spec, score)


def _repeated_key_name(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    repeated = _repeated_key_names([[candidate.left, candidate.right] for candidate in candidates])

    def score(candidate: OverlapCandidate):
        left = "_".join(sorted(_tokens(candidate.left.get("column", ""))))
        right = "_".join(sorted(_tokens(candidate.right.get("column", ""))))
        return (1.0 if left and left == right and left in repeated else 0.0, {"normalized_name": left})

    return _scalar_filter(candidates, spec, score)


def _shape_compatible(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, _context: FilterContext) -> list[OverlapCandidate]:
    def score(candidate: OverlapCandidate):
        return (
            1.0 if _shape_values_compatible(candidate.left, candidate.right) else 0.0,
            {
                "left_shape": _value_shape_class(candidate.left),
                "right_shape": _value_shape_class(candidate.right),
                "left_format": _column_shape_profile(candidate.left),
                "right_format": _column_shape_profile(candidate.right),
                "incompatible_reason": _shape_incompatible_reason(candidate.left, candidate.right),
            },
        )

    return _scalar_filter(candidates, spec, score)


def _value_overlap(candidates: list[OverlapCandidate], spec: OverlapFilterSpec, context: FilterContext) -> list[OverlapCandidate]:
    if not candidates:
        return []
    if context.db_connect is None and context.options.value_match_method != "metadata_sample":
        return []
    payloads = _detect_column_overlaps(
        context.db_connect,
        context.dialect,
        [(candidate.left, candidate.right) for candidate in candidates],
        context.options,
    )
    by_pair = {
        frozenset((str(payload["from_ref"]), str(payload["to_ref"]))): payload
        for payload in payloads
    }
    metric_name = "overlap_coefficient" if spec.metric == "score" else spec.metric
    retained: list[OverlapCandidate] = []
    for candidate in candidates:
        payload = by_pair.get(candidate.key)
        if payload is None:
            continue
        value_stats = dict(payload.get("stats") or {})
        score = float(value_stats.get("filter_score", value_stats.get(metric_name)) or 0.0)
        if score < spec.threshold:
            continue
        _record(candidate, spec, score, metrics=value_stats, value_match_method=context.options.value_match_method)
        candidate.value_overlap = payload
        retained.append(candidate)
    return retained


FILTER_RUNNERS: dict[str, FilterRunner] = {
    "same_schema": _same_schema,
    "different_table": _different_table,
    "different_table_group": _different_table_group,
    "table_group_representative": _table_group_representative,
    "name_token_overlap": _name_token_overlap,
    "domain_compatible": _domain_compatible,
    # Existing custom configs keep working while the named stage is migrated.
    "type_compatible": _domain_compatible,
    "key_like": _key_like,
    "repeated_key_name": _repeated_key_name,
    "shape_compatible": _shape_compatible,
    "value_overlap": _value_overlap,
}


def _overlap_payload(candidate: OverlapCandidate) -> dict[str, Any]:
    payload = deepcopy(candidate.value_overlap)
    payload["filter_evidence"] = candidate.filter_evidence
    payload["filter_pipeline"] = list(candidate.filter_evidence)
    payload["sources"] = sorted(set(payload.get("sources") or []) | {"filter_pipeline"})
    return payload
