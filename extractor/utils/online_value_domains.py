"""Experimental online clustering of columns into shared value domains.

The clusterer consumes already-built distinct-value hash sets.  It deliberately
does not know how those sets were extracted, so Snowflake and local databases
can use the same clustering logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Hashable, Iterable


@dataclass(frozen=True)
class ValueColumn:
    ref: str
    values: frozenset[int]
    bucket: Hashable = ""
    metadata: dict = field(default_factory=dict, compare=False)


@dataclass
class OnlineValueDomain:
    domain_id: int
    bucket: Hashable
    members: list[ValueColumn]
    union_values: set[int]
    anchors: list[ValueColumn]
    assignments: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class OnlineValueDomainConfig:
    overlap_threshold: float = 0.1
    match_policy: str = "union_and_anchor"
    anchor_overlap_threshold: float | None = None
    min_anchor_support: float = 0.5
    max_anchors: int = 8

    def __post_init__(self) -> None:
        if not 0.0 <= self.overlap_threshold <= 1.0:
            raise ValueError("overlap_threshold must be between 0 and 1")
        if self.match_policy not in {"union", "union_and_anchor"}:
            raise ValueError("match_policy must be 'union' or 'union_and_anchor'")
        if not 0.0 <= self.min_anchor_support <= 1.0:
            raise ValueError("min_anchor_support must be between 0 and 1")
        if self.max_anchors < 1:
            raise ValueError("max_anchors must be positive")


@dataclass(frozen=True)
class OnlineValueDomainResult:
    domains: tuple[OnlineValueDomain, ...]
    domain_comparisons: int
    anchor_comparisons: int
    assignments: dict[str, int]


def build_online_value_domains(
    columns: Iterable[ValueColumn],
    config: OnlineValueDomainConfig | None = None,
    *,
    compatible: Callable[[ValueColumn, OnlineValueDomain], bool] | None = None,
    minimum_overlap: Callable[[ValueColumn, ValueColumn], float] | None = None,
) -> OnlineValueDomainResult:
    """Scan columns once and assign each to its best compatible value domain.

    The requested ``union`` policy compares a column only with each domain's
    accumulated union.  ``union_and_anchor`` additionally requires support
    from a configurable fraction of representative member columns, which
    limits transitive A-B-C chain pollution while retaining the union as the
    primary index.
    """

    config = config or OnlineValueDomainConfig()
    domains: list[OnlineValueDomain] = []
    assignments: dict[str, int] = {}
    domain_comparisons = 0
    anchor_comparisons = 0

    for column in columns:
        if not column.values:
            continue
        matches: list[tuple[float, float, int, OnlineValueDomain, dict]] = []
        for domain in domains:
            if domain.bucket != column.bucket:
                continue
            if compatible is not None and not compatible(column, domain):
                continue
            domain_comparisons += 1
            anchor_thresholds = [
                minimum_overlap(column, anchor) if minimum_overlap is not None else config.overlap_threshold
                for anchor in domain.anchors
            ]
            union_threshold = min(anchor_thresholds, default=config.overlap_threshold)
            union_score = overlap_coefficient(column.values, domain.union_values)
            # A semantic match may lower the required coefficient to zero, but a
            # value domain still requires evidence of at least one shared value.
            if union_score <= 0.0 or union_score < union_threshold:
                continue

            anchor_scores: list[tuple[str, float, float]] = []
            support = 1.0
            best_anchor_score = union_score
            if config.match_policy == "union_and_anchor":
                configured_anchor_threshold = config.anchor_overlap_threshold
                for anchor, dynamic_threshold in zip(domain.anchors, anchor_thresholds):
                    anchor_comparisons += 1
                    score = overlap_coefficient(column.values, anchor.values)
                    threshold = configured_anchor_threshold if configured_anchor_threshold is not None else dynamic_threshold
                    anchor_scores.append((anchor.ref, score, threshold))
                supported = sum(
                    score > 0.0 and score >= threshold
                    for _, score, threshold in anchor_scores
                )
                support = supported / len(anchor_scores)
                best_anchor_score = max((score for _, score, _threshold in anchor_scores), default=0.0)
                if support < config.min_anchor_support:
                    continue

            evidence = {
                "column_ref": column.ref,
                "union_overlap": union_score,
                "anchor_support": support,
                "best_anchor_overlap": best_anchor_score,
                "anchor_scores": anchor_scores,
            }
            matches.append((union_score, best_anchor_score, -domain.domain_id, domain, evidence))

        if matches:
            _union_score, _anchor_score, _tie_break, domain, evidence = max(
                matches, key=lambda item: item[:3]
            )
            domain.members.append(column)
            domain.union_values.update(column.values)
            domain.assignments.append(evidence)
            if len(domain.anchors) < config.max_anchors:
                domain.anchors.append(column)
        else:
            domain = OnlineValueDomain(
                domain_id=len(domains),
                bucket=column.bucket,
                members=[column],
                union_values=set(column.values),
                anchors=[column],
            )
            domains.append(domain)
        assignments[column.ref] = domain.domain_id

    return OnlineValueDomainResult(
        domains=tuple(domains),
        domain_comparisons=domain_comparisons,
        anchor_comparisons=anchor_comparisons,
        assignments=assignments,
    )


def overlap_coefficient(left: set[int] | frozenset[int], right: set[int] | frozenset[int]) -> float:
    """Return |A intersect B| / min(|A|, |B|)."""

    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value in right for value in left) / len(left)
