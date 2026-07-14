"""SQL-derived guidance for BIRD result comparison.

This module produces comparison hints only.  It never decides correctness.
"""

from __future__ import annotations

import re


_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


def result_order_is_significant(sql: str | None) -> bool:
    """Return whether the golden query orders its outermost result."""

    if not sql:
        return False
    try:
        import sqlglot

        expression = sqlglot.parse_one(sql, read="sqlite")
        return expression.args.get("order") is not None
    except Exception:
        return bool(_ORDER_BY_RE.search(sql))


def sql_guided_column_mapping(
    golden_sql: str | None,
    predicted_sql: str | None,
    *,
    golden_width: int,
    predicted_width: int,
) -> tuple[int, ...] | None:
    """Return one unambiguous golden-to-predicted output mapping, if known."""

    golden_outputs = _sql_output_descriptors(golden_sql, golden_width)
    predicted_outputs = _sql_output_descriptors(predicted_sql, predicted_width)
    if golden_outputs is None or predicted_outputs is None:
        return None

    candidates: list[list[int]] = []
    for golden_output in golden_outputs:
        indexes = [
            index
            for index, predicted_output in enumerate(predicted_outputs)
            if golden_output & predicted_output
        ]
        if not indexes:
            return None
        candidates.append(indexes)

    mappings: list[tuple[int, ...]] = []

    def assign(position: int, used: set[int], current: list[int]) -> None:
        if len(mappings) > 1:
            return
        if position == len(candidates):
            mappings.append(tuple(current))
            return
        for index in candidates[position]:
            if index in used:
                continue
            used.add(index)
            current.append(index)
            assign(position + 1, used, current)
            current.pop()
            used.remove(index)

    assign(0, set(), [])
    return mappings[0] if len(mappings) == 1 else None


def _sql_output_descriptors(
    sql: str | None,
    result_width: int,
) -> tuple[frozenset[str], ...] | None:
    if not sql:
        return None
    try:
        import sqlglot
        from sqlglot import exp

        expression = sqlglot.parse_one(sql, read="sqlite")
        outputs = tuple(expression.selects)
        if len(outputs) != result_width or any(output.is_star for output in outputs):
            return None

        descriptors: list[frozenset[str]] = []
        for output in outputs:
            keys: set[str] = set()
            if output.alias:
                keys.add(f"alias:{output.alias.casefold()}")

            value = output.this if isinstance(output, exp.Alias) else output
            if isinstance(value, exp.Column):
                keys.add(f"column:{value.name.casefold()}")

            normalized = value.copy()
            for column in normalized.find_all(exp.Column):
                column.set("table", None)
                column.set("db", None)
                column.set("catalog", None)
            keys.add(f"expression:{normalized.sql(dialect='sqlite', normalize=True)}")
            descriptors.append(frozenset(keys))
        return tuple(descriptors)
    except Exception:
        return None
