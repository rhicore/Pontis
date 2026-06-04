"""SQL value grounding checks for literal predicates."""

from __future__ import annotations

import sqlite3
import re
from dataclasses import dataclass
from typing import Optional

import sqlglot
from sqlglot import exp

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
from agent.guardrail.sql_utils import extract_tables, get_sql_from_messages


@dataclass
class ValueIssue:
    severity: str
    table: str
    column: str
    operator: str
    value: str
    message: str


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _parse(sql: str) -> Optional[exp.Expression]:
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except sqlglot.errors.SqlglotError:
        return None


def _literal_value(node: exp.Expression) -> object | None:
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        try:
            return int(node.this)
        except (TypeError, ValueError):
            try:
                return float(node.this)
            except (TypeError, ValueError):
                return node.this
    return None


def _resolve_column_table(col: exp.Column, aliases: dict[str, str], single_table: str | None) -> tuple[str, str] | None:
    column = col.name
    table_prefix = col.table
    if table_prefix:
        return aliases.get(table_prefix.lower(), table_prefix), column
    if single_table:
        return single_table, column
    return None


def _iter_literal_predicates(sql: str):
    tree = _parse(sql)
    if tree is None:
        return

    tables, aliases = extract_tables(sql)
    single_table = next(iter(tables)) if len(tables) == 1 else None

    for eq in tree.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if isinstance(left, exp.Column):
            resolved = _resolve_column_table(left, aliases, single_table)
            value = _literal_value(right)
        elif isinstance(right, exp.Column):
            resolved = _resolve_column_table(right, aliases, single_table)
            value = _literal_value(left)
        else:
            resolved = None
            value = None
        if resolved and value is not None:
            yield ("=", resolved[0], resolved[1], value)

    for like in tree.find_all(exp.Like):
        left, right = like.this, like.expression
        if not isinstance(left, exp.Column):
            continue
        resolved = _resolve_column_table(left, aliases, single_table)
        value = _literal_value(right)
        if resolved and isinstance(value, str):
            yield ("LIKE", resolved[0], resolved[1], value)


def _literal_predicates_under_or(sql: str) -> set[tuple[str, str, str, str]]:
    """Return literal predicates that appear inside an OR expression.

    A single zero-hit branch in a disjunction is often an optional slot,
    synonym, or fallback candidate. Blocking that branch independently can
    create loops even when the whole SQL has grounded alternatives.
    """
    tree = _parse(sql)
    if tree is None:
        return set()

    tables, aliases = extract_tables(sql)
    single_table = next(iter(tables)) if len(tables) == 1 else None
    predicates: set[tuple[str, str, str, str]] = set()

    for or_node in tree.find_all(exp.Or):
        for node in or_node.walk():
            if isinstance(node, exp.EQ):
                left, right = node.left, node.right
                if isinstance(left, exp.Column):
                    resolved = _resolve_column_table(left, aliases, single_table)
                    value = _literal_value(right)
                elif isinstance(right, exp.Column):
                    resolved = _resolve_column_table(right, aliases, single_table)
                    value = _literal_value(left)
                else:
                    resolved = None
                    value = None
                if resolved and value is not None:
                    predicates.add(("=", resolved[0].lower(), resolved[1].lower(), str(value)))
            elif isinstance(node, exp.Like):
                left, right = node.this, node.expression
                if not isinstance(left, exp.Column):
                    continue
                resolved = _resolve_column_table(left, aliases, single_table)
                value = _literal_value(right)
                if resolved and isinstance(value, str):
                    predicates.add(("LIKE", resolved[0].lower(), resolved[1].lower(), str(value)))

    return predicates


def _empty_string_with_null_or_pairs(sql: str) -> set[tuple[str, str]]:
    """Return columns where ``col = ''`` is paired with ``col IS NULL`` in OR.

    Empty strings are often used as a missing-value alternative. If the SQL
    explicitly checks both NULL and empty string for the same column, a zero-hit
    empty string branch should not be treated as an ungrounded value by itself.
    """
    tree = _parse(sql)
    if tree is None:
        return set()

    tables, aliases = extract_tables(sql)
    single_table = next(iter(tables)) if len(tables) == 1 else None
    pairs: set[tuple[str, str]] = set()

    for or_node in tree.find_all(exp.Or):
        empty_columns: set[tuple[str, str]] = set()
        null_columns: set[tuple[str, str]] = set()
        for node in or_node.walk():
            if isinstance(node, exp.EQ):
                left, right = node.left, node.right
                if isinstance(left, exp.Column) and _literal_value(right) == "":
                    resolved = _resolve_column_table(left, aliases, single_table)
                elif isinstance(right, exp.Column) and _literal_value(left) == "":
                    resolved = _resolve_column_table(right, aliases, single_table)
                else:
                    resolved = None
                if resolved:
                    empty_columns.add((resolved[0].lower(), resolved[1].lower()))
            elif isinstance(node, exp.Is):
                left, right = node.this, node.expression
                if isinstance(left, exp.Column) and isinstance(right, exp.Null):
                    resolved = _resolve_column_table(left, aliases, single_table)
                    if resolved:
                        null_columns.add((resolved[0].lower(), resolved[1].lower()))
        pairs.update(empty_columns & null_columns)

    return pairs


def _db_connect_for_table(workspace, table: str):
    active = list(getattr(workspace, "active_projects", []) or [None])
    for project in active:
        rows = workspace.cypher(
            "MATCH (f:file)--(t) "
            "WHERE toLower(t.name) = toLower($table) "
            "AND any(label IN t.labels WHERE label IN ['table', 'view']) "
            "RETURN t LIMIT 5",
            params={"table": table},
            project=project,
        )
        for row in rows:
            meta = row.get("t") or {}
            connect = meta.get("_db_connect")
            if connect is not None:
                return connect, meta.get("table_name") or meta.get("view_name") or meta.get("name") or table
    return None, table


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    except Exception:
        return False
    return any(str(row[1]).lower() == column.lower() for row in rows)


def _column_declared_type(conn: sqlite3.Connection, table: str, column: str) -> str:
    try:
        rows = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    except Exception:
        return ""
    for row in rows:
        if str(row[1]).lower() == column.lower():
            return str(row[2] or "").upper()
    return ""


def _is_year_prefix_like(column: str, declared_type: str, pattern: str) -> bool:
    """Allow date/year prefix filters such as OpenDate LIKE '1980%'."""
    if not re.fullmatch(r"\d{4}%", str(pattern)):
        return False
    col_l = column.lower()
    type_u = declared_type.upper()
    return (
        "DATE" in type_u
        or "TIME" in type_u
        or col_l.endswith("date")
        or "date" in col_l
        or "year" in col_l
    )


def _is_date_period_like(column: str, declared_type: str, pattern: str) -> bool:
    """Allow empty date/month period filters; an empty period can be a valid answer."""
    text = str(pattern)
    if not re.fullmatch(r"%?\d{4}(?:-\d{2})?(?:-\d{2})?%?", text):
        return False
    col_l = column.lower()
    type_u = declared_type.upper()
    return (
        "DATE" in type_u
        or "TIME" in type_u
        or "date" in col_l
        or "time" in col_l
        or "year" in col_l
        or "month" in col_l
    )


def _count(conn: sqlite3.Connection, table: str, column: str, op: str, value: object) -> int:
    table_sql = _quote_ident(table)
    column_sql = _quote_ident(column)
    if op == "=":
        sql = f"SELECT COUNT(*) FROM {table_sql} WHERE {column_sql} = ?"
    else:
        sql = f"SELECT COUNT(*) FROM {table_sql} WHERE {column_sql} LIKE ?"
    return int(conn.execute(sql, (value,)).fetchone()[0])


def _total_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0])


def _distinct_count(conn: sqlite3.Connection, table: str, column: str) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(DISTINCT {_quote_ident(column)}) FROM {_quote_ident(table)}"
        ).fetchone()[0]
    )


def _top_values(conn: sqlite3.Connection, table: str, column: str, limit: int = 8) -> str:
    rows = conn.execute(
        f"SELECT {_quote_ident(column)} AS v, COUNT(*) AS c "
        f"FROM {_quote_ident(table)} "
        f"WHERE {_quote_ident(column)} IS NOT NULL "
        f"GROUP BY {_quote_ident(column)} "
        "ORDER BY c DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return "(无非空样例)"
    return ", ".join(f"{row[0]!r}({row[1]})" for row in rows)


def _exact_candidate_from_like(pattern: str) -> str | None:
    if "%" not in pattern and "_" not in pattern:
        return pattern
    if pattern.startswith("%") and pattern.endswith("%"):
        inner = pattern[1:-1]
        if inner and "%" not in inner and "_" not in inner:
            return inner
    return None


class SQLValueGroundingCheck(Guardrail):
    """Check final SQL literal predicates against actual column values."""

    def __init__(self, *, enum_distinct_threshold: int = 100, broad_ratio: float = 0.2):
        self.enum_distinct_threshold = enum_distinct_threshold
        self.broad_ratio = broad_ratio
        self._last_block_signature: tuple[str, tuple[tuple[str, str, str, str, str], ...]] | None = None
        self._repeat_count = 0

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}
        if not ctx.pending_calls:
            sql = get_sql_from_messages(ctx.messages)
            if sql:
                issues = self._check_sql(ctx.workspace, sql)
                blocking = [issue for issue in issues if issue.severity == "block"]
                if blocking:
                    signature = self._signature(sql, blocking)
                    if signature == self._last_block_signature:
                        self._repeat_count += 1
                    else:
                        self._last_block_signature = signature
                        self._repeat_count = 1
                    result["text"] = CallVerdict(
                        "block",
                        self._format(blocking, repeat_count=self._repeat_count),
                    )
                else:
                    self._last_block_signature = None
                    self._repeat_count = 0
        return result

    @staticmethod
    def _signature(sql: str, issues: list[ValueIssue]) -> tuple[str, tuple[tuple[str, str, str, str, str], ...]]:
        normalized_sql = re.sub(r"\s+", " ", (sql or "").strip()).lower()
        issue_key = tuple(
            sorted(
                (
                    issue.table.lower(),
                    issue.column.lower(),
                    issue.operator.upper(),
                    issue.value,
                    issue.message,
                )
                for issue in issues
            )
        )
        return normalized_sql, issue_key

    def _check_sql(self, workspace, sql: str) -> list[ValueIssue]:
        issues: list[ValueIssue] = []
        if workspace is None:
            return issues
        empty_string_null_or_pairs = _empty_string_with_null_or_pairs(sql)
        or_literal_predicates = _literal_predicates_under_or(sql)

        for op, table, column, value in _iter_literal_predicates(sql) or []:
            connect, physical_table = _db_connect_for_table(workspace, table)
            if connect is None:
                continue
            try:
                conn = connect(readonly=True)
            except Exception:
                continue
            try:
                if not _column_exists(conn, physical_table, column):
                    continue
                declared_type = _column_declared_type(conn, physical_table, column)
                if op == "=":
                    if (
                        value == ""
                        and (table.lower(), column.lower()) in empty_string_null_or_pairs
                    ):
                        continue
                    exact_count = _count(conn, physical_table, column, "=", value)
                    if exact_count == 0:
                        if (
                            op,
                            table.lower(),
                            column.lower(),
                            str(value),
                        ) in or_literal_predicates:
                            continue
                        top = _top_values(conn, physical_table, column)
                        issues.append(ValueIssue(
                            "block",
                            table,
                            column,
                            op,
                            str(value),
                            f"`{table}.{column} = {value!r}` 在数据库中命中 0 行；该列高频值: {top}",
                        ))
                    continue

                like_count = _count(conn, physical_table, column, "LIKE", value)
                if like_count == 0:
                    if (
                        op,
                        table.lower(),
                        column.lower(),
                        str(value),
                    ) in or_literal_predicates:
                        continue
                    if _is_date_period_like(column, declared_type, str(value)):
                        continue
                    top = _top_values(conn, physical_table, column)
                    issues.append(ValueIssue(
                        "block",
                        table,
                        column,
                        op,
                        str(value),
                        f"`{table}.{column} LIKE {value!r}` 在数据库中命中 0 行；该列高频值: {top}",
                    ))
                    continue

                exact_candidate = _exact_candidate_from_like(str(value))
                exact_count = (
                    _count(conn, physical_table, column, "=", exact_candidate)
                    if exact_candidate is not None
                    else 0
                )
                distinct = _distinct_count(conn, physical_table, column)
                total = max(_total_count(conn, physical_table), 1)
                ratio = like_count / total

                if _is_year_prefix_like(column, declared_type, str(value)):
                    continue

                if (
                    exact_candidate is not None
                    and exact_count > 0
                ):
                    issues.append(ValueIssue(
                        "block",
                        table,
                        column,
                        op,
                        str(value),
                        f"`{table}.{column}` 存在精确值 {exact_candidate!r}（{exact_count} 行），当前 LIKE 命中 {like_count} 行；优先使用精确匹配。",
                    ))
                elif (
                    distinct <= self.enum_distinct_threshold
                    and ("%" in str(value) or "_" in str(value))
                ):
                    top = _top_values(conn, physical_table, column)
                    issues.append(ValueIssue(
                        "block",
                        table,
                        column,
                        op,
                        str(value),
                        f"`{table}.{column}` 是低基数列（distinct={distinct}），LIKE 命中 {like_count} 行；请从枚举值中选择精确值。高频值: {top}",
                    ))
                elif ratio >= self.broad_ratio and like_count > 20:
                    issues.append(ValueIssue(
                        "block",
                        table,
                        column,
                        op,
                        str(value),
                        f"`{table}.{column} LIKE {value!r}` 命中 {like_count}/{total} 行（{ratio:.1%}），过滤过宽；请确认是否需要精确值。",
                    ))
            except Exception:
                continue
            finally:
                conn.close()
        return issues

    def _format(self, issues: list[ValueIssue], *, repeat_count: int = 1) -> str:
        lines = ["以下 SQL 过滤值需要重新 grounding："]
        if repeat_count > 1:
            lines.append(
                f"这是同一 SQL 第 {repeat_count} 次触发相同 value-grounding 拦截；"
                "不要原样输出上一版 SQL，必须修改对应的谓词或 CASE 条件。"
            )
        for issue in issues[:8]:
            lines.append(f"  - {issue.message}")
        lines.append(
            "请根据列的真实取值重写相关谓词、WHERE、LIKE 或 CASE 条件；"
            "若列中存在精确枚举值，优先使用等值匹配。"
        )
        return "\n".join(lines)
