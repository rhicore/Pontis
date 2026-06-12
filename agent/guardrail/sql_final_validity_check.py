"""Final SQL validity checks.

This guardrail is intentionally dataset-agnostic. It only verifies that the
final text response is a clean, parseable, read-only SQL block whose referenced
schema exists in the active workspace/database.
"""
from __future__ import annotations

import re
from typing import Optional

import sqlglot
from sqlglot import exp

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext
from agent.guardrail.sql_utils import extract_col_refs, extract_tables


_SQL_BLOCK_RE = re.compile(r"```sql\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_UNKNOWN = object()


class FinalSQLValidityCheck(Guardrail):
    """Block malformed or non-executable final SQL text responses."""

    def check(self, ctx: GuardrailContext) -> dict:
        if ctx.pending_calls:
            return {}

        text = (ctx.last_response or "").strip()
        if not text:
            return {}

        sql, message = _extract_single_sql_block(text)
        if message:
            return {"text": CallVerdict("block", message)}

        issues = _validate_sql(ctx.workspace, sql)
        if issues:
            body = "\n".join(f"  - {issue}" for issue in issues[:12])
            return {
                "text": CallVerdict(
                    "block",
                    "最终 SQL 未通过通用有效性检查：\n"
                    + body
                    + "\n\n请修正为一条可解析、只读且能在当前数据库 schema 中编译的 SQLite SELECT，并只输出 SQL 代码块。",
                )
            }
        return {}


def _extract_single_sql_block(text: str) -> tuple[str, str]:
    blocks = [m.group(1).strip() for m in _SQL_BLOCK_RE.finditer(text)]
    if len(blocks) != 1:
        return "", "最终输出必须包含且只包含一个 ```sql``` 代码块；请只输出最终 SQL。"

    outside = _SQL_BLOCK_RE.sub("", text).strip()
    if outside:
        return "", "最终输出不应包含 SQL 代码块之外的解释文字；请只输出最终 SQL。"

    sql = _strip_trailing_semicolon(blocks[0])
    if not sql:
        return "", "SQL 代码块为空；请输出一条 SQLite SELECT。"
    return sql, ""


def _strip_trailing_semicolon(sql: str) -> str:
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].strip()
    return sql


def _validate_sql(workspace, sql: str) -> list[str]:
    issues: list[str] = []
    tree = _parse(sql)
    if tree is None:
        return ["SQL 无法按 SQLite 语法解析。"]

    if not _is_read_only_select(sql, tree):
        issues.append("最终 SQL 必须是一条只读 SELECT/WITH SELECT 查询。")

    issues.extend(_schema_issues(workspace, sql, tree))

    exec_issue = _compile_issue(workspace, sql, tree)
    if exec_issue:
        issues.append(exec_issue)

    return issues


def _parse(sql: str) -> Optional[exp.Expression]:
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except sqlglot.errors.SqlglotError:
        return None


def _is_read_only_select(sql: str, tree: exp.Expression) -> bool:
    head = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
    if head not in {"select", "with"}:
        return False
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
    )
    return not any(isinstance(node, forbidden) for node in tree.walk())


def _schema_issues(workspace, sql: str, tree: exp.Expression) -> list[str]:
    if workspace is None:
        return []

    ctes = _cte_names(tree)
    tables, aliases = extract_tables(sql)
    real_tables = {table for table in tables if table.lower() not in ctes}
    issues: list[str] = []

    for table in sorted(real_tables, key=str.lower):
        table_meta = _resolve_table(workspace, table)
        if table_meta is False:
            issues.append(f"表 `{table}` 不存在于当前 workspace schema。")

    for table, column in extract_col_refs(sql, aliases):
        if table.lower() in ctes:
            continue
        column_meta = _resolve_column(workspace, table, column)
        if column_meta is False:
            issues.append(f"列 `{table}.{column}` 不存在于当前 workspace schema。")

    return issues


def _cte_names(tree: exp.Expression) -> set[str]:
    names = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            names.add(alias.lower())
    return names


def _resolve_table(workspace, table: str):
    rows = _cypher_optional(
        workspace,
        "MATCH (t) "
        "WHERE toLower(t.name) = toLower($table) "
        "AND any(label IN coalesce(t.labels, []) WHERE label IN ['table', 'view']) "
        "RETURN t LIMIT 2",
        params={"table": table},
    )
    if rows is _UNKNOWN:
        return _UNKNOWN
    return rows[0].get("t") if len(rows) == 1 else False


def _resolve_column(workspace, table: str, column: str):
    rows = _cypher_optional(
        workspace,
        "MATCH (t)--(c:col) "
        "WHERE toLower(t.name) = toLower($table) "
        "AND toLower(c.name) = toLower($column) "
        "RETURN c LIMIT 2",
        params={"table": table, "column": column},
    )
    if rows is _UNKNOWN:
        return _UNKNOWN
    return rows[0].get("c") if len(rows) == 1 else False


def _compile_issue(workspace, sql: str, tree: exp.Expression) -> str:
    connect = _db_connect_for_sql(workspace, tree)
    if connect is None:
        return ""
    try:
        conn = connect(readonly=True)
    except Exception:
        return ""
    try:
        conn.execute("EXPLAIN QUERY PLAN " + _strip_trailing_semicolon(sql))
        return ""
    except Exception as exc:
        return f"SQLite 无法编译该 SQL：{type(exc).__name__}: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_connect_for_sql(workspace, tree: exp.Expression):
    if workspace is None:
        return None
    ctes = _cte_names(tree)
    for table_expr in tree.find_all(exp.Table):
        table = table_expr.name
        if not table or table.lower() in ctes:
            continue
        rows = _cypher_optional(
            workspace,
            "MATCH (f:file)--(t) "
            "WHERE toLower(t.name) = toLower($table) "
            "AND any(label IN coalesce(t.labels, []) WHERE label IN ['table', 'view']) "
            "RETURN coalesce(t._db_connect, f._db_connect, t.db_connect, f.db_connect) AS db_connect "
            "LIMIT 2",
            params={"table": table},
        )
        if rows is _UNKNOWN:
            return None
        if len(rows) == 1:
            connect = rows[0].get("db_connect")
            if connect is not None:
                return connect
    return None


def _cypher_optional(workspace, query: str, params: dict):
    try:
        return workspace.cypher(query, params=params)
    except Exception:
        return _UNKNOWN
