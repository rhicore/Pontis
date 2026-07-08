"""DB FK Validate — validate FK entities against database contents.

All access goes through storage-exposed graph handles. FK/table/column metadata
is read from KG nodes, and SQL execution uses ``_db_connect``/``db_connect``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from extractor.utils.refs import set_entity_meta
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

TRANSIENT_RETRY_LIMIT = 4


@dataclass(frozen=True)
class _FKTask:
    fk_ref: str
    fk_name: str
    db_connect: Any
    dialect: str
    from_schema: str
    from_table: str
    from_col: str
    to_schema: str
    to_table: str
    to_col: str


def generate(workspace: Workspace) -> None:
    """Validate all FK entities with executable database handles."""
    logger.info("=== Validating FK data consistency ===")

    tasks = _load_fk_tasks(workspace)
    if not tasks:
        logger.info("  No FK entities found")
        return

    validated = 0
    for task in tasks:
        if _validate_with_retries(task, workspace):
            validated += 1

    logger.info("  Validated %s/%s FK entities", validated, len(tasks))


def _load_fk_tasks(workspace: Workspace) -> list[_FKTask]:
    rows = workspace.cypher(
        """
        MATCH (f:fk)
        OPTIONAL MATCH (d:db)-[:RELATED_TO*1..3]-(f)
        WITH f, d, coalesce(f._db_connect, f.db_connect, d._db_connect, d.db_connect) AS db_connect
        WHERE db_connect IS NOT NULL
        RETURN DISTINCT f, d, db_connect
        """
    )
    tasks: list[_FKTask] = []
    for row in rows:
        fk = row.get("f") or {}
        db_node = row.get("d") or {}
        db_connect = row.get("db_connect")
        if not callable(db_connect):
            continue
        task = _build_task(workspace, fk, db_node, db_connect)
        if task:
            tasks.append(task)
    return tasks


def _build_task(workspace: Workspace, fk: dict, db_node: dict, db_connect) -> _FKTask | None:
    fk_ref = _node_ref(fk)
    fk_name = str(fk.get("name") or fk_ref)
    if not fk_ref:
        return None

    dialect = str(getattr(db_connect, "dialect", "") or fk.get("dialect") or db_node.get("dialect") or "sqlite").lower()

    from_col_ref = str(fk.get("_from_col_ref") or fk.get("from_col_ref") or "")
    to_col_ref = str(fk.get("_to_col_ref") or fk.get("to_col_ref") or "")
    from_ctx = _column_context(workspace, from_col_ref) if from_col_ref else None
    to_ctx = _column_context(workspace, to_col_ref) if to_col_ref else None

    parsed = _parse_fk_entity(fk_name)
    from_table = str(fk.get("from_table") or fk.get("source_table") or (from_ctx or {}).get("table_name") or parsed.get("from_table", ""))
    from_col = str(fk.get("from_column") or _single_value(fk.get("source_columns")) or (from_ctx or {}).get("col_name") or parsed.get("from_col", ""))
    to_table = str(fk.get("to_table") or fk.get("target_table") or (to_ctx or {}).get("table_name") or parsed.get("to_table", ""))
    to_col = str(fk.get("to_column") or _single_value(fk.get("target_columns")) or (to_ctx or {}).get("col_name") or parsed.get("to_col", ""))

    if not from_ctx and from_table and from_col:
        from_ctx = _table_column_context(workspace, fk, from_table, from_col, source_ref_hint=_source_table_ref_from_fk(fk_ref))
    if not to_ctx and to_table and to_col:
        to_ctx = _table_column_context(workspace, fk, to_table, to_col)

    from_schema = str((from_ctx or {}).get("schema_name") or fk.get("from_schema") or fk.get("source_schema") or "")
    to_schema = str((to_ctx or {}).get("schema_name") or fk.get("to_schema") or fk.get("target_schema") or "")
    from_table = str((from_ctx or {}).get("table_name") or from_table).split(".")[-1]
    to_table = str((to_ctx or {}).get("table_name") or to_table).split(".")[-1]
    from_col = str((from_ctx or {}).get("col_name") or from_col)
    to_col = str((to_ctx or {}).get("col_name") or to_col)

    if not all([from_table, from_col, to_table, to_col]):
        return None

    return _FKTask(
        fk_ref=fk_ref,
        fk_name=fk_name,
        db_connect=db_connect,
        dialect=dialect,
        from_schema=from_schema,
        from_table=from_table,
        from_col=from_col,
        to_schema=to_schema,
        to_table=to_table,
        to_col=to_col,
    )


def _node_ref(node: dict) -> str:
    return str(node.get("_ref") or node.get("ref") or node.get("path") or node.get("name") or "")


def _single_value(value) -> str:
    if isinstance(value, list) and len(value) == 1:
        return str(value[0])
    if isinstance(value, tuple) and len(value) == 1:
        return str(value[0])
    return ""


def _source_table_ref_from_fk(fk_ref: str) -> str:
    if "--fk--" in fk_ref:
        return fk_ref.split("--fk--", 1)[0]
    return ""


def _column_context(workspace: Workspace, col_ref: str) -> dict | None:
    rows = workspace.cypher(
        """
        MATCH (c:col)
        WHERE coalesce(c._ref, c.ref, c.path, c.name) = $col_ref
        MATCH (t)-[:RELATED_TO]-(c)
        WHERE t:table OR t:view
        OPTIONAL MATCH (s:schema)-[:RELATED_TO]-(t)
        RETURN c, t, s
        LIMIT 1
        """,
        params={"col_ref": col_ref},
    )
    return _context_from_row(rows[0]) if rows else None


def _table_column_context(
    workspace: Workspace,
    fk: dict,
    table_name: str,
    col_name: str,
    *,
    source_ref_hint: str = "",
) -> dict | None:
    if source_ref_hint:
        rows = workspace.cypher(
            """
            MATCH (t)-[:RELATED_TO]-(c:col)
            WHERE (t:table OR t:view)
              AND coalesce(t._ref, t.ref, t.path, t.name) = $table_ref
              AND (c.name = $col_name OR c.column_name = $col_name)
            OPTIONAL MATCH (s:schema)-[:RELATED_TO]-(t)
            RETURN c, t, s
            LIMIT 1
            """,
            params={"table_ref": source_ref_hint, "col_name": col_name},
        )
        if rows:
            return _context_from_row(rows[0])

    db_ref = str(fk.get("_db_ref") or fk.get("db_ref") or "")
    rows = workspace.cypher(
        """
        MATCH (t)-[:RELATED_TO]-(c:col)
        WHERE (t:table OR t:view)
          AND (t.name = $table_name OR t.table_name = $table_name OR t.name = $raw_table_name)
          AND (c.name = $col_name OR c.column_name = $col_name)
          AND ($db_ref = '' OR coalesce(t._db_ref, c._db_ref, '') = $db_ref)
        OPTIONAL MATCH (s:schema)-[:RELATED_TO]-(t)
        RETURN c, t, s
        LIMIT 1
        """,
        params={
            "table_name": str(table_name).split(".")[-1],
            "raw_table_name": table_name,
            "col_name": col_name,
            "db_ref": db_ref,
        },
    )
    return _context_from_row(rows[0]) if rows else None


def _context_from_row(row: dict) -> dict:
    col = row.get("c") or {}
    table = row.get("t") or {}
    schema = row.get("s") or {}
    return {
        "col_ref": _node_ref(col),
        "table_ref": _node_ref(table),
        "schema_name": schema.get("schema_name") or schema.get("name") or table.get("schema_name") or "",
        "table_name": table.get("table_name") or table.get("name") or "",
        "col_name": col.get("column_name") or col.get("name") or "",
    }


def _validate_with_retries(task: _FKTask, workspace: Workspace) -> bool:
    """Run one FK validation, retrying transient Neo4j write conflicts."""
    for attempt in range(1, TRANSIENT_RETRY_LIMIT + 1):
        try:
            return _validate_one(task, workspace)
        except Exception as e:
            if not _is_transient_neo4j_error(e) or attempt == TRANSIENT_RETRY_LIMIT:
                logger.warning("  Failed to validate %s: %s", task.fk_name, e)
                return False
            wait_s = min(0.5 * attempt, 2.0)
            logger.warning(
                "  Retry FK validation after transient Neo4j error "
                "(%s/%s) for %s: %s",
                attempt,
                TRANSIENT_RETRY_LIMIT,
                task.fk_name,
                e,
            )
            time.sleep(wait_s)
    return False


def _is_transient_neo4j_error(error: Exception) -> bool:
    text = str(error)
    return (
        "Neo.TransientError" in text
        or "DeadlockDetected" in text
        or "deadlock detected" in text.lower()
    )


def _parse_fk_entity(entity_name: str) -> dict:
    """Parse legacy FK names like ``from_table.from_col->to_table.to_col``."""
    if "->" not in entity_name:
        return {}

    from_part, to_part = entity_name.split("->", 1)
    from_segments = from_part.split(".", 1)
    to_segments = to_part.split(".", 1)

    if len(from_segments) < 2 or len(to_segments) < 2:
        return {}

    return {
        "from_table": from_segments[0],
        "from_col": from_segments[1],
        "to_table": to_segments[0],
        "to_col": to_segments[1],
    }


def _validate_one(task: _FKTask, workspace: Workspace) -> bool:
    try:
        conn = _connect(task.db_connect, readonly=True)
        try:
            total, matched, format_hint = _run_fk_queries(conn, task)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("  Query failed for %s: %s", task.fk_name, e)
        return False

    if total == 0:
        return False

    match_rate = matched / total
    violation_count = total - matched
    update = {
        "match_rate": round(match_rate, 4),
        "violation_count": violation_count,
        "total_count": total,
    }
    if format_hint:
        update["format_hint"] = format_hint

    set_entity_meta(workspace, task.fk_ref, update)

    status = "OK" if match_rate == 1.0 else f"MISMATCH {match_rate * 100:.1f}%"
    logger.info("  %s: %s (%s/%s)", task.fk_name, status, matched, total)
    return True


def _run_fk_queries(conn, task: _FKTask) -> tuple[int, int, str | None]:
    ft = _qualified_table_sql(task.from_schema, task.from_table, task.dialect)
    tt = _qualified_table_sql(task.to_schema, task.to_table, task.dialect)
    fc = _quote_identifier(task.from_col)
    tc = _quote_identifier(task.to_col)

    total = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
    matched = conn.execute(
        f"SELECT COUNT(*) FROM {ft} t "
        f"WHERE EXISTS (SELECT 1 FROM {tt} s WHERE s.{tc} = t.{fc})"
    ).fetchone()[0]

    violation_count = total - matched
    format_hint = None
    if violation_count > 0:
        try:
            fixed = conn.execute(
                f"SELECT COUNT(*) FROM {ft} t "
                f"WHERE NOT EXISTS (SELECT 1 FROM {tt} s WHERE s.{tc} = t.{fc}) "
                f"  AND EXISTS (SELECT 1 FROM {tt} s WHERE s.{tc} = '0' || t.{fc})"
            ).fetchone()[0]
            if fixed == violation_count and fixed > 0:
                len_f = conn.execute(f"SELECT MAX(LENGTH({fc})) FROM {ft}").fetchone()[0] or 0
                len_t = conn.execute(f"SELECT MAX(LENGTH({tc})) FROM {tt}").fetchone()[0] or 0
                format_hint = (
                    f"发现 {violation_count} 条 FK 违规，全部可通过在 {task.from_table}.{task.from_col} 前补 '0' 修复。"
                    f"推测 {task.from_table}.{task.from_col} 部分值缺少前导零（{len_f}位），"
                    f"而 {task.to_table}.{task.to_col} 为完整格式（{len_t}位）。"
                )
            elif fixed > 0:
                format_hint = (
                    f"发现 {violation_count} 条 FK 违规，其中 {fixed} 条可通过补 '0' 修复，"
                    f"剩余 {violation_count - fixed} 条为其他数据不一致。"
                )
        except Exception:
            pass

        if not format_hint:
            try:
                cast_fixed = conn.execute(
                    f"SELECT COUNT(*) FROM {ft} t "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {tt} s WHERE s.{tc} = t.{fc}) "
                    f"  AND EXISTS (SELECT 1 FROM {tt} s "
                    f"WHERE CAST(s.{tc} AS INTEGER) = CAST(t.{fc} AS INTEGER))"
                ).fetchone()[0]
                if cast_fixed == violation_count and cast_fixed > 0:
                    format_hint = (
                        f"发现 {violation_count} 条 FK 违规，全部可通过 CAST AS INTEGER 修复。"
                        f"推测存在数值类型/字符串格式不一致。"
                    )
            except Exception:
                pass

    return total, matched, format_hint


def _connect(db_connect, *, readonly: bool):
    try:
        return db_connect(readonly=readonly)
    except TypeError:
        return db_connect()


def _qualified_table_sql(schema_name: str, table_name: str, dialect: str) -> str:
    if schema_name and dialect not in {"sqlite", "duckdb"}:
        return f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
    return _quote_identifier(table_name)


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python -m extractor.db_fk_validate <project_path>")
        sys.exit(1)
    ws = Workspace(project_path=sys.argv[1])
    generate(ws)
