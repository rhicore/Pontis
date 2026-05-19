"""Query inspector for source-module triggering.

This is not a Cypher executor. Neo4j executes the user's query. This module
only extracts enough structure for source modules to decide whether they need
to publish virtual subgraphs before Neo4j runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_labels(labels: List[str]) -> List[str]:
    """Return labels with duplicates removed while preserving order."""
    result = []
    seen = set()
    for label in labels or []:
        if label and label not in seen:
            result.append(label)
            seen.add(label)
    return result


def is_valid_label(label: str) -> bool:
    """Return whether a label is safe to embed in Cypher syntax."""
    return isinstance(label, str) and bool(_LABEL_RE.fullmatch(label))


def cypher_label_clause(labels: List[str]) -> str:
    """Build a safe Cypher label clause like ``:table:col``."""
    return "".join(
        f":`{label}`"
        for label in normalize_labels(labels)
        if is_valid_label(label)
    )


def label_matches(entity_labels: List[str], query: str) -> bool:
    return query in set(entity_labels or [])


def labels_match_all(entity_labels: List[str], queries: List[str]) -> bool:
    return set(queries or []).issubset(set(entity_labels or []))


@dataclass
class NodePattern:
    var: str
    labels: List[str] = field(default_factory=list)
    props: Dict[str, Union[str, Any]] = field(default_factory=dict)

    def matches_labels(self, entity_labels: List[str]) -> bool:
        if not self.labels:
            return True
        return labels_match_all(entity_labels, self.labels)


@dataclass
class RelPattern:
    from_var: str
    to_var: str
    min_hops: int = 1
    max_hops: int = 1


@dataclass
class WhereClause:
    var: str
    prop: str
    op: str
    value: Union[str, Any]


@dataclass
class SetClause:
    var: str
    prop: str = ""
    value: Union[str, Any] = ""
    param_name: Optional[str] = None
    is_merge: bool = False


@dataclass
class ReturnItem:
    expr: str
    alias: str


@dataclass
class CypherQuery:
    nodes: List[NodePattern] = field(default_factory=list)
    rels: List[RelPattern] = field(default_factory=list)
    where: List[WhereClause] = field(default_factory=list)
    return_vars: List[str] = field(default_factory=list)
    return_items: List[ReturnItem] = field(default_factory=list)
    action: str = "RETURN"
    set_clauses: List[SetClause] = field(default_factory=list)
    create_rels: List[tuple] = field(default_factory=list)
    params: dict = field(default_factory=dict)


_NODE_RE = re.compile(
    r"\((\w+)"
    r"((?::`?[\w]+`?)*)"
    r"(?:\s*\{([^}]*)\})?"
    r"\)"
)
_REL_VAR_RE = re.compile(r"-\[\*(\d+)\.\.(\d*)\]-")
_RETURN_RE = re.compile(r"\bRETURN\b\s+(.+)", re.IGNORECASE | re.DOTALL)
_CREATE_RE = re.compile(r"\bCREATE\b", re.IGNORECASE)
_DELETE_RE = re.compile(r"\bDELETE\b", re.IGNORECASE)
_SET_RE = re.compile(r"\bSET\b", re.IGNORECASE)
_WHERE_RE = re.compile(
    r"""(\w+)\.(\w+)\s*"""
    r"""(STARTS\s+WITH|ENDS\s+WITH|CONTAINS|!=|>=|<=|=|>|<)\s*"""
    r"""(?:['"]([^'"]*)['"]|(-?[\d]+(?:\.[\d]+)?)|\$(\w+))""",
    re.IGNORECASE,
)
_SET_MERGE_RE = re.compile(r"""(\w+)\s*\+=\s*\$(\w+)""")
_SET_PROP_RE = re.compile(
    r"""(\w+)\.(\w+)\s*=\s*(?:['"]([^'"]*)['"]|(-?[\d]+(?:\.[\d]+)?)|\$(\w+))"""
)


def _parse_value(text: str, params: dict | None = None):
    value = text.strip()
    if value.startswith("$"):
        return (params or {}).get(value[1:])
    if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _parse_props(text: str, params: dict | None = None) -> Dict[str, Union[str, Any]]:
    props: Dict[str, Union[str, Any]] = {}
    if not text:
        return props
    for part in re.split(r",\s*", text):
        if not part or ":" not in part:
            continue
        key, raw_value = part.split(":", 1)
        props[key.strip().strip("`")] = _parse_value(raw_value, params=params)
    return props


def _strip_tail_clauses(text: str) -> str:
    return re.split(
        r"\b(ORDER\s+BY|LIMIT|SKIP|WITH|UNION)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()


def _parse_return_items(text: str) -> tuple[list[str], list[ReturnItem]]:
    match = _RETURN_RE.search(text)
    if not match:
        return [], []
    body = _strip_tail_clauses(match.group(1))
    vars_: list[str] = []
    items: list[ReturnItem] = []
    for raw in [p.strip() for p in body.split(",") if p.strip()]:
        alias_match = re.match(r"(.+?)\s+AS\s+(\w+)$", raw, flags=re.IGNORECASE)
        if alias_match:
            expr = alias_match.group(1).strip()
            alias = alias_match.group(2).strip()
            items.append(ReturnItem(expr=expr, alias=alias))
            if re.fullmatch(r"\w+", expr):
                vars_.append(expr)
            continue
        if re.fullmatch(r"\w+", raw):
            vars_.append(raw)
            items.append(ReturnItem(expr=raw, alias=raw))
        else:
            alias = raw.split(".")[-1] if "." in raw else raw
            items.append(ReturnItem(expr=raw, alias=alias))
    return vars_, items


def _parse_where(text: str, params: dict | None = None) -> list[WhereClause]:
    clauses: list[WhereClause] = []
    for match in _WHERE_RE.finditer(text):
        raw_value = (
            match.group(4)
            if match.group(4) is not None
            else match.group(5)
            if match.group(5) is not None
            else f"${match.group(6)}"
        )
        clauses.append(
            WhereClause(
                var=match.group(1),
                prop=match.group(2),
                op=match.group(3).upper(),
                value=_parse_value(raw_value, params=params),
            )
        )
    return clauses


def _parse_set(text: str, params: dict | None = None) -> list[SetClause]:
    clauses: list[SetClause] = []
    set_match = _SET_RE.search(text)
    if not set_match:
        return clauses
    body = _strip_tail_clauses(text[set_match.end():])
    for raw in [p.strip() for p in body.split(",") if p.strip()]:
        merge_match = _SET_MERGE_RE.match(raw)
        if merge_match:
            clauses.append(
                SetClause(
                    var=merge_match.group(1),
                    param_name=merge_match.group(2),
                    is_merge=True,
                )
            )
            continue
        prop_match = _SET_PROP_RE.match(raw)
        if not prop_match:
            continue
        raw_value = (
            prop_match.group(3)
            if prop_match.group(3) is not None
            else prop_match.group(4)
            if prop_match.group(4) is not None
            else f"${prop_match.group(5)}"
        )
        clauses.append(
            SetClause(
                var=prop_match.group(1),
                prop=prop_match.group(2),
                value=_parse_value(raw_value, params=params),
                param_name=prop_match.group(5),
            )
        )
    return clauses


def parse_cypher(text: str, params: dict | None = None) -> CypherQuery:
    params = params or {}
    action = "RETURN"
    if _CREATE_RE.search(text):
        action = "CREATE"
    elif _DELETE_RE.search(text):
        action = "DELETE"
    elif _SET_RE.search(text):
        action = "SET"

    nodes: list[NodePattern] = []
    for match in _NODE_RE.finditer(text):
        labels_str = match.group(2) or ""
        labels = [
            label.strip("`")
            for label in labels_str.split(":")
            if label.strip("`")
        ]
        nodes.append(
            NodePattern(
                var=match.group(1),
                labels=labels,
                props=_parse_props(match.group(3) or "", params=params),
            )
        )

    rels: list[RelPattern] = []
    for idx in range(len(nodes) - 1):
        start = text.find(f"({nodes[idx].var}")
        end = text.find(f"({nodes[idx + 1].var}", start + 1)
        between = text[start:end] if start >= 0 and end >= 0 else ""
        rel_match = _REL_VAR_RE.search(between)
        if rel_match:
            max_s = rel_match.group(2)
            rels.append(
                RelPattern(
                    from_var=nodes[idx].var,
                    to_var=nodes[idx + 1].var,
                    min_hops=int(rel_match.group(1)),
                    max_hops=int(max_s) if max_s else 0,
                )
            )
        elif "--" in between or "]-" in between or "-[" in between:
            rels.append(RelPattern(from_var=nodes[idx].var, to_var=nodes[idx + 1].var))

    create_rels: list[tuple] = []
    if action == "CREATE" and re.search(r"\bMATCH\b", text, re.IGNORECASE):
        create_match = _CREATE_RE.search(text)
        if create_match:
            edge_nodes = _NODE_RE.findall(text[create_match.end():])
            if len(edge_nodes) >= 2:
                create_rels.append((edge_nodes[0][0], edge_nodes[1][0]))

    return_vars, return_items = _parse_return_items(text)
    return CypherQuery(
        nodes=nodes,
        rels=rels,
        where=_parse_where(text, params=params),
        return_vars=return_vars,
        return_items=return_items,
        action=action,
        set_clauses=_parse_set(text, params=params),
        create_rels=create_rels,
        params=params,
    )
