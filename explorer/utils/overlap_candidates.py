"""Shared overlap candidate rendering utilities for explorer agents."""
import json
from dataclasses import dataclass, replace

from storage.workspace import Workspace


MAX_CANDIDATES_PER_AGENT = 30


@dataclass(frozen=True)
class ColumnInfo:
    ref: str
    name: str
    table: str
    brief: str = ""
    official_column_description: str = ""
    official_value_description: str = ""
    disambig_links: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateGroup:
    source: str
    title: str
    columns: tuple[ColumnInfo, ...]
    note: str = ""
    relation_ref: str = ""


def column_ref(node: dict, table_name: str = "") -> str:
    for key in ("path", "_ref"):
        value = node.get(key)
        if value:
            return str(value)
    name = str(node.get("name") or "")
    if table_name:
        return f"{table_name}/{name}:col"
    return f"{name}:col"


def build_candidate_groups(workspace: Workspace) -> list[CandidateGroup]:
    groups = relation_candidate_groups(workspace, "overlap")

    deduped: list[CandidateGroup] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        key = tuple(sorted(col.ref for col in group.columns))
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        deduped.append(group)

    return deduped


def candidate_batches(groups: list[CandidateGroup], batch_size: int) -> list[list[CandidateGroup]]:
    return [groups[idx:idx + batch_size] for idx in range(0, len(groups), batch_size)]


def relation_candidate_groups(workspace: Workspace, label: str) -> list[CandidateGroup]:
    rows = workspace.cypher(
        """
        MATCH (r)--(c)
        WHERE $label IN coalesce(r.labels, [])
          AND 'col' IN coalesce(c.labels, [])
        OPTIONAL MATCH (t)--(c)
        WHERE any(table_label IN coalesce(t.labels, []) WHERE table_label IN ['table', 'view', 'csv_table'])
        WITH r, collect(DISTINCT {col: c, table_name: t.name}) AS endpoints
        WHERE size(endpoints) >= 2
        RETURN r AS r, endpoints AS endpoints
        ORDER BY r.name
        """,
        params={"label": label},
    )
    groups: list[CandidateGroup] = []
    for row in rows:
        rel = row.get("r") or {}
        cols = [
            _column_info(item.get("col") or {}, str(item.get("table_name") or ""))
            for item in row.get("endpoints") or []
        ]
        selected = _with_disambig_links(workspace, _sorted_columns(cols))
        if len(selected) < 2:
            continue
        groups.append(CandidateGroup(
            source=label,
            title=str(rel.get("name") or label),
            columns=selected,
            note=_relation_note(rel, label),
            relation_ref=_relation_ref(rel, label),
        ))
    return groups


def _column_info(node: dict, table_name: str = "") -> ColumnInfo:
    return ColumnInfo(
        ref=column_ref(node, table_name),
        name=str(node.get("name") or ""),
        table=table_name,
        brief=str(node.get("brief") or "")[:120],
        official_column_description=str(node.get("official_column_description") or "")[:180],
        official_value_description=str(node.get("official_value_description") or "")[:180],
    )


def _sorted_columns(columns: list[ColumnInfo]) -> tuple[ColumnInfo, ...]:
    unique = {col.ref: col for col in columns if col.ref}
    return tuple(sorted(unique.values(), key=lambda c: (c.table, c.name, c.ref)))


def _with_disambig_links(workspace: Workspace, columns: tuple[ColumnInfo, ...]) -> tuple[ColumnInfo, ...]:
    refs = [col.ref for col in columns if col.ref]
    if not refs:
        return columns

    rows = workspace.cypher(
        """
        MATCH (c)
        WHERE 'col' IN coalesce(c.labels, [])
          AND (c.path IN $refs OR c._ref IN $refs)
        WITH c, coalesce(c.path, c._ref) AS col_ref
        OPTIONAL MATCH (c)--(d)
        WHERE 'disambig' IN coalesce(d.labels, [])
        OPTIONAL MATCH (d)--(dc)
        WHERE 'col' IN coalesce(dc.labels, [])
        OPTIONAL MATCH (dt)--(dc)
        WHERE any(table_label IN coalesce(dt.labels, []) WHERE table_label IN ['table', 'view', 'csv_table'])
        WITH col_ref, d
        WHERE d IS NOT NULL
        RETURN col_ref AS ref,
               collect(DISTINCT d.name) AS links
        """,
        params={"refs": refs},
    )
    links_by_ref: dict[str, tuple[str, ...]] = {}
    for row in rows:
        links = []
        for link in row.get("links") or []:
            name = str(link or "")
            if name:
                links.append(name)
        links_by_ref[str(row.get("ref") or "")] = tuple(sorted(set(links)))

    return tuple(
        replace(col, disambig_links=links_by_ref.get(col.ref, ()))
        for col in columns
    )


def _relation_ref(rel: dict, label: str) -> str:
    name = str(rel.get("name") or "")
    if not name:
        return ""
    return name if name.endswith(f":{label}") else f"{name}:{label}"


def _relation_note(rel: dict, label: str) -> str:
    brief = str(rel.get("brief") or "").strip()
    if brief:
        return brief[:220]
    if label != "overlap":
        return ""

    parts = []
    sources = _json_value(rel.get("sources"))
    if sources:
        parts.append(f"sources={sources}")

    stats = _json_value(rel.get("stats"))
    if isinstance(stats, dict):
        stat_parts = []
        for key in (
            "column_count",
            "pair_count",
            "card_overlap",
            "min_card_overlap",
            "max_card_overlap",
            "jaccard",
            "min_jaccard",
            "max_jaccard",
            "coverage_A_in_B",
            "coverage_B_in_A",
        ):
            if key not in stats:
                continue
            stat_parts.append(f"{key}={stats[key]}")
        if stat_parts:
            parts.append(", ".join(stat_parts))

    return " | ".join(parts)[:260]


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
