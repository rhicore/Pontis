"""Cypher scoping helpers for logical Pontis projects."""

from __future__ import annotations

import re
from typing import Any


_NODE_PATTERN_RE = re.compile(
    r"(?<![\w])"
    r"\(\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)?"
    r"\s*"
    r"((?::`?[A-Za-z_][A-Za-z0-9_]*`?)*)"
    r"\s*"
    r"(\{[^{}]*\})?"
    r"\s*\)"
)
_PROJECT_PROP_VALUE_RE = re.compile(r"(^|,\s*)`?project`?\s*:\s*[^,}]+")
_PROJECT_PROP_MUTATION_RE = re.compile(
    r"\b(?:SET|REMOVE)\b[^\n;]*\.\s*`?project`?\b",
    re.IGNORECASE,
)
_NODE_REPLACEMENT_RE = re.compile(
    r"\bSET\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*\$[A-Za-z_][A-Za-z0-9_]*",
    re.IGNORECASE,
)
_PROJECT_LITERAL_RE = re.compile(r"""`?project`?\s*:\s*(['"])(.*?)\1""")
_PROJECT_PARAM_RE = re.compile(r"""`?project`?\s*:\s*\$([A-Za-z_][A-Za-z0-9_]*)""")
_WHERE_PROJECT_EQ_LITERAL_RE = re.compile(
    r"""(?:\b\w+\.)`?project`?\s*=\s*(['"])(.*?)\1""",
    re.IGNORECASE,
)
_WHERE_PROJECT_EQ_PARAM_RE = re.compile(
    r"""(?:\b\w+\.)`?project`?\s*=\s*\$([A-Za-z_][A-Za-z0-9_]*)""",
    re.IGNORECASE,
)
_WHERE_PROJECT_IN_LITERAL_RE = re.compile(
    r"""(?:\b\w+\.)`?project`?\s+IN\s*\[([^\]]*)\]""",
    re.IGNORECASE,
)
_WHERE_PROJECT_IN_PARAM_RE = re.compile(
    r"""(?:\b\w+\.)`?project`?\s+IN\s+\$([A-Za-z_][A-Za-z0-9_]*)""",
    re.IGNORECASE,
)
_STRING_LITERAL_RE = re.compile(r"""(['"])(.*?)\1""")


def scope_user_cypher(query: str, params: dict[str, Any] | None, project: str) -> tuple[str, dict[str, Any]]:
    """Restrict a user Cypher query to one logical Pontis project.

    This is intentionally applied only at the Workspace user-query boundary.
    Source modules still publish with their own internal Cypher.
    """
    validate_user_cypher(query, params)
    if not project:
        return query, dict(params or {})

    scoped_params = dict(params or {})
    project_param = _fresh_param(scoped_params)
    scoped_params[project_param] = project

    def replace(match: re.Match) -> str:
        original = match.group(0)
        props = match.group(3) or ""

        var = match.group(1) or ""
        labels = match.group(2) or ""
        head = f"{var}{labels}"
        project_prop = f"project: ${project_param}"
        if props:
            body = props[1:-1].strip()
            if _PROJECT_PROP_VALUE_RE.search(body):
                body = _PROJECT_PROP_VALUE_RE.sub(rf"\1{project_prop}", body)
                new_props = "{" + body + "}"
            else:
                new_props = "{" + project_prop + (", " + body if body else "") + "}"
        else:
            new_props = "{" + project_prop + "}"
        return f"({head} {new_props})" if head else f"({new_props})"

    return _replace_outside_strings(query, replace), scoped_params


def validate_user_cypher(query: str, params: dict[str, Any] | None = None) -> None:
    _reject_project_mutation(query, params)


def requested_projects_from_cypher(query: str, params: dict[str, Any] | None = None) -> set[str] | None:
    """Extract simple user-requested project filters from Cypher."""
    requested: set[str] = set()
    params = params or {}

    for match in _PROJECT_LITERAL_RE.finditer(query):
        requested.add(match.group(2))
    for match in _WHERE_PROJECT_EQ_LITERAL_RE.finditer(query):
        requested.add(match.group(2))
    for match in _WHERE_PROJECT_IN_LITERAL_RE.finditer(query):
        for literal in _STRING_LITERAL_RE.finditer(match.group(1)):
            requested.add(literal.group(2))

    for pattern in (_PROJECT_PARAM_RE, _WHERE_PROJECT_EQ_PARAM_RE, _WHERE_PROJECT_IN_PARAM_RE):
        for match in pattern.finditer(query):
            value = params.get(match.group(1))
            if isinstance(value, str):
                requested.add(value)
            elif isinstance(value, (list, tuple, set)):
                requested.update(str(item) for item in value if isinstance(item, str))

    return requested or None


def _reject_project_mutation(query: str, params: dict[str, Any] | None) -> None:
    if _PROJECT_PROP_MUTATION_RE.search(query):
        raise ValueError("Cypher may not SET or REMOVE the reserved project property")
    if _NODE_REPLACEMENT_RE.search(query):
        raise ValueError("Cypher may not replace whole nodes because project scoping must be preserved")
    for value in (params or {}).values():
        if isinstance(value, dict) and "project" in value:
            raise ValueError("Cypher params may not include reserved project properties")


def _fresh_param(params: dict[str, Any]) -> str:
    name = "__pontis_project"
    while name in params:
        name = "_" + name
    return name


def _replace_outside_strings(query: str, replace) -> str:
    result: list[str] = []
    start = 0
    idx = 0
    quote = ""
    while idx < len(query):
        ch = query[idx]
        if quote:
            if ch == "\\":
                idx += 2
                continue
            if ch == quote:
                quote = ""
            idx += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            idx += 1
            continue
        match = _NODE_PATTERN_RE.match(query, idx)
        if match:
            result.append(query[start:idx])
            result.append(replace(match))
            idx = match.end()
            start = idx
            continue
        idx += 1
    result.append(query[start:])
    return "".join(result)


__all__ = ["requested_projects_from_cypher", "scope_user_cypher", "validate_user_cypher"]
