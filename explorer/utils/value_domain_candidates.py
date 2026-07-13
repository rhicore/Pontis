"""Load and render value-domain review candidates from the graph."""
from __future__ import annotations

import json
from dataclasses import dataclass

from storage.workspace import Workspace


MAX_DOMAINS_PER_AGENT = 12


@dataclass(frozen=True)
class ValueDomainMember:
    ref: str
    name: str
    kind: str
    schema: str = ""
    table: str = ""
    data_type: str = ""
    role: str = ""
    member_count: int = 1
    official_column_description: str = ""
    official_value_description: str = ""
    brief: str = ""
    cardinality: int | None = None
    sample: str = ""
    topk: str = ""
    physical_members: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValueDomainCandidate:
    ref: str
    name: str
    schema: str
    review_status: str
    union_cardinality: int | None
    semantic_roles: str
    overlap_metric: str
    overlap_threshold: float | None
    min_anchor_support: float | None
    extraction_evidence: str
    members: tuple[ValueDomainMember, ...]


def build_value_domain_candidates(
    workspace: Workspace,
    *,
    statuses: tuple[str, ...] = ("pending_review",),
) -> list[ValueDomainCandidate]:
    """Return value domains and their physical/logical comparison units."""

    rows = workspace.cypher(
        """
        MATCH (d)--(m)
        WHERE 'value_domain' IN coalesce(d.labels, [])
          AND coalesce(d.review_status, 'pending_review') IN $statuses
          AND (
            'col' IN coalesce(m.labels, [])
            OR 'logical_col' IN coalesce(m.labels, [])
          )
        OPTIONAL MATCH (t)--(m)
        WHERE any(label IN coalesce(t.labels, []) WHERE label IN ['table', 'view'])
        OPTIONAL MATCH (m)--(pc)
        WHERE 'logical_col' IN coalesce(m.labels, [])
          AND 'col' IN coalesce(pc.labels, [])
        OPTIONAL MATCH (pt)--(pc)
        WHERE any(label IN coalesce(pt.labels, []) WHERE label IN ['table', 'view'])
        WITH d, m,
             collect(DISTINCT t.name) AS direct_tables,
             collect(DISTINCT {
               ref: coalesce(pc.path, pc._ref),
               table_name: pt.name,
               column_name: pc.name
             }) AS physical_members
        RETURN d AS domain, m AS member,
               direct_tables AS direct_tables,
               physical_members AS physical_members
        ORDER BY d.name, m.name
        """,
        params={"statuses": list(statuses)},
    )

    grouped: dict[str, dict] = {}
    for row in rows:
        domain = row.get("domain") or {}
        ref = _node_ref(domain)
        if not ref:
            continue
        entry = grouped.setdefault(ref, {"domain": domain, "members": {}})
        member = _member_info(
            row.get("member") or {},
            row.get("direct_tables") or [],
            row.get("physical_members") or [],
        )
        if member.ref:
            entry["members"][member.ref] = member

    candidates = []
    for ref, entry in grouped.items():
        domain = entry["domain"]
        members = tuple(sorted(
            entry["members"].values(),
            key=lambda member: (member.kind, member.schema, member.table, member.name, member.ref),
        ))
        if len(members) < 2:
            continue
        candidates.append(ValueDomainCandidate(
            ref=ref,
            name=str(domain.get("name") or "value_domain"),
            schema=str(domain.get("schema_name") or ""),
            review_status=str(domain.get("review_status") or "pending_review"),
            union_cardinality=_optional_int(domain.get("union_cardinality")),
            semantic_roles=_compact(domain.get("semantic_roles"), 240),
            overlap_metric=str(domain.get("overlap_metric") or ""),
            overlap_threshold=_optional_float(domain.get("overlap_threshold")),
            min_anchor_support=_optional_float(domain.get("min_anchor_support")),
            extraction_evidence=_compact(domain.get("extraction_evidence"), 900),
            members=members,
        ))
    return sorted(candidates, key=lambda candidate: (candidate.schema, candidate.name, candidate.ref))


def candidate_batches(
    candidates: list[ValueDomainCandidate],
    batch_size: int = MAX_DOMAINS_PER_AGENT,
) -> list[list[ValueDomainCandidate]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [candidates[index:index + batch_size] for index in range(0, len(candidates), batch_size)]


def _member_info(node: dict, direct_tables: list, physical_members: list) -> ValueDomainMember:
    labels = set(node.get("labels") or [])
    logical = "logical_col" in labels
    table_names = sorted({str(value) for value in direct_tables if value})
    physical_refs = []
    for item in physical_members:
        ref = str(item.get("ref") or "")
        table = str(item.get("table_name") or "")
        column = str(item.get("column_name") or "")
        if ref:
            physical_refs.append(ref)
        elif table or column:
            physical_refs.append(".".join(part for part in (table, column) if part))
    return ValueDomainMember(
        ref=_node_ref(node),
        name=str(node.get("name") or node.get("role") or ""),
        kind="logical_col" if logical else "col",
        schema=str(node.get("schema_name") or ""),
        table=", ".join(table_names),
        data_type=str(node.get("data_type") or _type_label(labels)),
        role=str(node.get("role") or ""),
        member_count=int(node.get("member_count") or len(physical_refs) or 1),
        official_column_description=_compact(node.get("official_column_description"), 180),
        official_value_description=_compact(node.get("official_value_description"), 180),
        brief=_compact(node.get("brief"), 140),
        cardinality=_optional_int(node.get("cardinality")),
        sample=_compact(node.get("sample"), 220),
        topk=_compact(node.get("topk"), 220),
        physical_members=tuple(sorted(set(physical_refs))),
    )


def _node_ref(node: dict) -> str:
    return str(node.get("path") or node.get("_ref") or "")


def _type_label(labels: set[str]) -> str:
    ignored = {"col", "logical_col", "grouped", "standalone"}
    return ",".join(sorted(labels - ignored))


def _compact(value, limit: int) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.split())
    return text[:limit]


def _optional_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
