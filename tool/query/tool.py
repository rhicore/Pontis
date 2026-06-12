"""Query tool — execute read-only SQL over DB or tabular file refs."""
import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time

from tool.utils.workspace_access import resolve_file_sources, workspace_allows_direct_fs

logger = logging.getLogger(__name__)

# 匹配非 SELECT 的写操作关键词
_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|ATTACH|DETACH)\b",
    re.IGNORECASE,
)

_DEFAULT_LIMIT = 20
_DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
_MAX_RESULT_CHARS = 8000
_JSON_RECORD_KEYS = ("records", "data", "items", "rows", "results")
_TYPE_SAMPLE_ROWS = 5000
_CSV_CACHE_FORMAT_VERSION = "typed-v1"
_INTEGER_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")
_REAL_RE = re.compile(
    r"^[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][+-]?\d+)?$"
)


def _strip_leading_sql_comments(sql: str) -> str:
    stripped = sql.strip()
    while stripped:
        if stripped.startswith("--"):
            newline = stripped.find("\n")
            if newline < 0:
                return ""
            stripped = stripped[newline + 1:].lstrip()
            continue
        if stripped.startswith("/*"):
            end = stripped.find("*/")
            if end < 0:
                return ""
            stripped = stripped[end + 2:].lstrip()
            continue
        break
    return stripped


def _is_readonly_sql(sql: str) -> bool:
    stripped = _strip_leading_sql_comments(sql)
    if not stripped:
        return False

    upper = stripped.upper()
    if upper.startswith("PRAGMA"):
        return True
    if upper.startswith("SELECT"):
        return True
    if upper.startswith("WITH"):
        return True
    return False


def _sqlite_error_hint(sql: str, error: Exception) -> str:
    message = str(error)
    lower = message.lower()
    upper_sql = sql.upper()
    hints: list[str] = []
    if "no such function: lpad" in lower or "LPAD(" in upper_sql:
        hints.append("SQLite 左填充可用 printf/substr 组合表达。")
    if "ambiguous column name" in lower:
        hints.append("同名列需要添加表名或别名前缀。")
    if "syntax error" in lower and "UNION" in upper_sql and "LIMIT" in upper_sql:
        hints.append("复合查询各分支的 ORDER BY/LIMIT 可先放入 WITH 或子查询。")
    return ("；".join(hints)) if hints else ""


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _safe_table_alias(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0] or "data"
    alias = re.sub(r"\W+", "_", stem).strip("_")
    if not alias or alias[0].isdigit():
        alias = f"table_{alias or 'data'}"
    return alias


def _dedupe_columns(headers: list[str]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        name = str(raw or "").strip() or f"column_{idx + 1}"
        count = seen.get(name, 0) + 1
        seen[name] = count
        out.append(name if count == 1 else f"{name}_{count}")
    return out


def _to_sql_value(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False)


def _blank_to_null(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
    return value


def _numeric_kind(value) -> str:
    value = _blank_to_null(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "integer"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER_RE.match(text):
            digits = text[1:] if text[:1] in {"+", "-"} else text
            if len(digits) > 1 and digits.startswith("0"):
                return "text"
            return "integer"
        if _REAL_RE.match(text):
            return "real"
    return "text"


def _infer_sql_type(values) -> str:
    saw_value = False
    saw_real = False
    for value in values:
        kind = _numeric_kind(value)
        if kind == "null":
            continue
        saw_value = True
        if kind == "text":
            return "TEXT"
        if kind == "real":
            saw_real = True
    if not saw_value:
        return "TEXT"
    return "REAL" if saw_real else "INTEGER"


def _coerce_for_sql_type(value, sql_type: str):
    value = _blank_to_null(value)
    if value is None:
        return None
    if sql_type == "INTEGER":
        if isinstance(value, bool):
            return 1 if value else 0
        try:
            return int(str(value).strip()) if not isinstance(value, int) else value
        except (TypeError, ValueError):
            return str(value)
    if sql_type == "REAL":
        try:
            return float(str(value).strip()) if not isinstance(value, (int, float)) else value
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_csv_row(row: list[str], width: int) -> list:
    if len(row) < width:
        row = row + [None] * (width - len(row))
    elif len(row) > width:
        row = row[:width]
    return [_blank_to_null(value) for value in row]


def _iter_sources(workspace, labels: tuple[str, ...]) -> list:
    sources = []
    seen = set()
    for label in labels:
        for source in resolve_file_sources(workspace, ".", labels=(label,), allow_directory=True):
            if source.path in seen:
                continue
            seen.add(source.path)
            sources.append(source)
    return sorted(sources, key=lambda item: item.path)


def _unique_aliases(rows: list[dict]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        alias = row.get("alias")
        if not alias:
            continue
        counts[alias] = counts.get(alias, 0) + 1
    for row in rows:
        alias = row.get("alias")
        row["use_alias"] = bool(alias and counts.get(alias) == 1 and alias != row.get("table"))


def _csv_cache_path(source) -> str:
    token = "|".join(str(part or "") for part in (
        _CSV_CACHE_FORMAT_VERSION,
        source.path,
        source.file_size,
        source.line_count,
        source.char_count,
    ))
    digest = hashlib.sha1(token.encode("utf-8", errors="ignore")).hexdigest()[:24]
    cache_dir = os.path.join(tempfile.gettempdir(), f"pontis_query_cache_{os.getuid()}")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{digest}.sqlite")


def _format_query_result(columns: list[str], rows: list[tuple], has_more: bool, limit: int) -> str:
    if not columns:
        return "(查询无结果)"

    lines = []
    col_widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val if val is not None else "NULL")))

    col_widths = [min(w, 40) for w in col_widths]
    header = " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns))
    sep = "-+-".join("-" * w for w in col_widths)
    lines.append(header)
    lines.append(sep)

    for row in rows:
        vals = []
        for i, val in enumerate(row):
            s = str(val if val is not None else "NULL")
            if len(s) > 40:
                s = s[:37] + "..."
            vals.append(s.ljust(col_widths[i]))
        lines.append(" | ".join(vals))

    result = "\n".join(lines)
    if len(result) > _MAX_RESULT_CHARS:
        result = result[:_MAX_RESULT_CHARS] + "\n... (截断)"
    if has_more:
        result += f"\n(结果超过 {limit} 行，仅显示前 {limit} 行)"
    return result


def _available_tables_text(ref: str, tables: list[dict]) -> str:
    if not tables:
        return f'Available tables in ref="{ref}": (none)'
    lines = [f'Available tables in ref="{ref}":']
    for table in tables[:80]:
        alias = table.get("alias") if table.get("use_alias") else ""
        alias_text = f", alias: {alias}" if alias else ""
        lines.append(f"- {table['table']} (source: {table.get('source', '')}{alias_text})")
    if len(tables) > 80:
        lines.append(f"... {len(tables) - 80} more")
    return "\n".join(lines)


def _fetch_rows(
    conn: sqlite3.Connection,
    sql: str,
    limit: int,
    timeout_seconds: float = _DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> tuple[list[str], list[tuple], bool]:
    deadline = time.monotonic() + max(0.1, timeout_seconds)

    def check_timeout() -> int:
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(check_timeout, 10_000)
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max(0, limit) + 1)
        has_more = len(rows) > limit
        return columns, rows[:limit], has_more
    except sqlite3.OperationalError as exc:
        if time.monotonic() > deadline and "interrupted" in str(exc).lower():
            raise TimeoutError(f"SQL query timed out after {timeout_seconds:.0f}s") from exc
        raise
    finally:
        conn.set_progress_handler(None, 0)


def _resolve_csv_source(workspace, selector: str):
    sources = []
    sources.extend(resolve_file_sources(workspace, selector, labels=("csv",), allow_directory=False))
    sources.extend(resolve_file_sources(workspace, selector, labels=("tsv",), allow_directory=False))
    deduped = []
    seen = set()
    for source in sources:
        if source.path in seen:
            continue
        seen.add(source.path)
        deduped.append(source)
    if len(deduped) == 1:
        return deduped[0], None
    if len(deduped) > 1:
        options = "\n".join(f"- {src.path}" for src in deduped[:20])
        return None, f"错误：表格文件 ref 不唯一: {selector}\n{options}"
    return None, None


def _build_csv_cache(source, cache_path: str) -> None:
    delimiter = "\t" if source.path.lower().endswith(".tsv") else ","
    tmp = tempfile.NamedTemporaryFile(
        prefix="pontis_query_build_",
        suffix=".sqlite",
        dir=os.path.dirname(cache_path),
        delete=False,
    )
    tmp_path = tmp.name
    tmp.close()
    conn = None
    try:
        conn = sqlite3.connect(tmp_path)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")

        with source.open_file("r", encoding="utf-8", errors="ignore", newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            headers = next(reader, None)
            if not headers:
                raise ValueError(f"表格文件没有表头: {source.path}")
            _load_csv_rows(conn, "this", headers, reader)
            alias = _safe_table_alias(source.path)
            if alias != "this":
                conn.execute(f"CREATE VIEW {_quote_ident(alias)} AS SELECT * FROM {_quote_ident('this')}")
        conn.commit()
        conn.close()
        conn = None
        os.replace(tmp_path, cache_path)
    finally:
        if conn is not None:
            conn.close()
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _query_csv_source(sql: str, source, limit: int) -> str:
    alias = _safe_table_alias(source.path)
    table_names = ["this"]
    if alias != "this":
        table_names.append(alias)

    cache_path = _csv_cache_path(source)
    conn = None
    try:
        if not os.path.exists(cache_path):
            _build_csv_cache(source, cache_path)
        conn = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    except Exception as e:
        if os.path.exists(cache_path):
            try:
                os.unlink(cache_path)
            except OSError:
                pass
        aliases = ", ".join(table_names)
        hint = _sqlite_error_hint(sql, e)
        hint_line = f"\n修正方向: {hint}" if hint else ""
        return (
            f"SQL 执行错误: {type(e).__name__}: {e}{hint_line}\n"
            f"Available tables for ref=\"{source.path}\": {aliases}"
        )

    try:
        cols, rows, has_more = _fetch_rows(conn, sql, limit)
        return _format_query_result(cols, rows, has_more, limit)
    except Exception as e:
        aliases = ", ".join(table_names)
        hint = _sqlite_error_hint(sql, e)
        hint_line = f"\n修正方向: {hint}" if hint else ""
        return (
            f"SQL 执行错误: {type(e).__name__}: {e}{hint_line}\n"
            f"Available tables for ref=\"{source.path}\": {aliases}"
        )
    finally:
        if conn is not None:
            conn.close()


def _load_csv_rows(conn: sqlite3.Connection, table_name: str, headers: list[str], rows_iter) -> None:
    columns = _dedupe_columns(headers)
    sample_rows = []
    for row in rows_iter:
        sample_rows.append(_normalize_csv_row(row, len(columns)))
        if len(sample_rows) >= _TYPE_SAMPLE_ROWS:
            break

    col_types = []
    for idx in range(len(columns)):
        col_types.append(_infer_sql_type(row[idx] for row in sample_rows))

    col_defs = ", ".join(
        f"{_quote_ident(col)} {col_types[idx]}" for idx, col in enumerate(columns)
    )
    conn.execute(f"CREATE TABLE {_quote_ident(table_name)} ({col_defs})")
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {_quote_ident(table_name)} VALUES ({placeholders})"

    def coerce_row(row):
        return [
            _coerce_for_sql_type(value, col_types[idx])
            for idx, value in enumerate(_normalize_csv_row(row, len(columns)))
        ]

    batch = [coerce_row(row) for row in sample_rows]
    for row in rows_iter:
        batch.append(coerce_row(row))
        if len(batch) >= 1000:
            conn.executemany(insert_sql, batch)
            batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)


def _load_csv_source_into(conn: sqlite3.Connection, source, table_name: str) -> None:
    delimiter = "\t" if source.path.lower().endswith(".tsv") else ","
    with source.open_file("r", encoding="utf-8", errors="ignore", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        headers = next(reader, None)
        if not headers:
            raise ValueError(f"表格文件没有表头: {source.path}")
        _load_csv_rows(conn, table_name, headers, reader)


def _find_json_records(data):
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data, "$"
    if isinstance(data, dict):
        for key in _JSON_RECORD_KEYS:
            value = data.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value, f"$.{key}"
    return None, ""


def _resolve_json_source(workspace, selector: str):
    sources = resolve_file_sources(workspace, selector, labels=("json",), allow_directory=False)
    if not sources and selector.lower().endswith(".json"):
        fallback = resolve_file_sources(workspace, selector, allow_directory=False)
        sources = [src for src in fallback if "json" in src.labels or src.path.lower().endswith(".json")]
    if len(sources) == 1:
        return sources[0], None
    if len(sources) > 1:
        options = "\n".join(f"- {src.path}" for src in sources[:20])
        return None, f"错误：JSON 文件 ref 不唯一: {selector}\n{options}"
    return None, None


def _load_json_source_into(conn: sqlite3.Connection, source, table_name: str) -> str:
    with source.open_file("r", encoding="utf-8", errors="ignore") as fh:
        data = json.load(fh)
    records, pointer = _find_json_records(data)
    if records is None:
        raise ValueError("未找到可查询的 list[dict] records；请先用 jd 确认 JSON 结构")

    headers = []
    seen = set()
    for item in records[:1000]:
        for key in item.keys():
            key_text = str(key)
            if key_text not in seen:
                seen.add(key_text)
                headers.append(key_text)
    if not headers:
        raise ValueError("JSON records 没有字段")

    columns = _dedupe_columns(headers)
    col_types = []
    sample_records = records[:_TYPE_SAMPLE_ROWS]
    for header in headers:
        col_types.append(_infer_sql_type(_to_sql_value(item.get(header)) for item in sample_records))

    col_defs = ", ".join(
        f"{_quote_ident(col)} {col_types[idx]}" for idx, col in enumerate(columns)
    )
    conn.execute(f"CREATE TABLE {_quote_ident(table_name)} ({col_defs})")
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {_quote_ident(table_name)} VALUES ({placeholders})"
    batch = []
    for item in records:
        row = [
            _coerce_for_sql_type(_to_sql_value(item.get(header)), col_types[idx])
            for idx, header in enumerate(headers)
        ]
        batch.append(row)
        if len(batch) >= 1000:
            conn.executemany(insert_sql, batch)
            batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)
    return pointer


def _query_json_source(sql: str, source, limit: int) -> str:
    alias = _safe_table_alias(source.path)
    table_names = ["this"]
    if alias != "this":
        table_names.append(alias)

    conn = None
    try:
        conn = sqlite3.connect(":memory:")
        _load_json_source_into(conn, source, "this")
        if alias != "this":
            conn.execute(f"CREATE VIEW {_quote_ident(alias)} AS SELECT * FROM {_quote_ident('this')}")
        cols, rows, has_more = _fetch_rows(conn, sql, limit)
        return _format_query_result(cols, rows, has_more, limit)
    except Exception as e:
        aliases = ", ".join(table_names)
        hint = _sqlite_error_hint(sql, e)
        hint_line = f"\n修正方向: {hint}" if hint else ""
        return (
            f"SQL 执行错误: {type(e).__name__}: {e}{hint_line}\n"
            f"Available tables for ref=\"{source.path}\": {aliases}"
        )
    finally:
        if conn is not None:
            conn.close()


def _resolve_db_connection(workspace, selector: str):
    if os.path.isabs(selector):
        if not workspace_allows_direct_fs(workspace):
            return None, None, "错误：当前 workspace 不允许绕过 storage 直接访问数据库文件。请使用图中已投影的数据库文件 ref。"
        root = os.path.abspath(getattr(workspace, "project_path", "") or "")
        abs_file = os.path.abspath(selector)
        if root and not (abs_file == root or abs_file.startswith(root + os.sep)):
            return None, None, "错误：数据库文件路径不在当前 workspace source 根目录内。"
        if not os.path.isfile(selector):
            return None, None, f"错误：数据库文件不存在: {selector}"
        return None, selector, None

    active_projects = set(getattr(workspace, "active_projects", []) or [])
    if selector in active_projects:
        rows = workspace.cypher(
            "MATCH (f:file:db) "
            "RETURN f.path AS path, coalesce(f._db_connect, f.db_connect) AS db_connect",
            project=selector,
        )
        if len(rows) == 1:
            db_connect = rows[0].get("db_connect")
            db_path = getattr(db_connect, "db_path", None) if db_connect is not None else None
            if db_connect is not None and db_path:
                return db_connect, db_path, None

    selector_head = selector.split(":", 1)[0].strip()
    selector_stem = os.path.splitext(os.path.basename(selector_head))[0].strip()
    if selector_stem and selector_stem in active_projects:
        rows = workspace.cypher(
            "MATCH (f:file:db) "
            "RETURN f.path AS path, coalesce(f._db_connect, f.db_connect) AS db_connect",
            project=selector_stem,
        )
        if len(rows) == 1:
            db_connect = rows[0].get("db_connect")
            db_path = getattr(db_connect, "db_path", None) if db_connect is not None else None
            if db_connect is not None and db_path:
                return db_connect, db_path, None

    sources = resolve_file_sources(workspace, selector, labels=("db",), allow_directory=False)
    if len(sources) > 1:
        options = "\n".join(f"- {src.path}" for src in sources[:20])
        return None, None, f"错误：数据库文件 ref 不唯一: {selector}\n{options}"

    project_root = getattr(workspace, "project_path", "") or ""
    if sources:
        source_path = sources[0].path
    else:
        source_path = selector

    direct_path = os.path.realpath(os.path.join(project_root, source_path)) if project_root else ""
    if (
        workspace_allows_direct_fs(workspace)
        and project_root
        and direct_path
        and os.path.commonpath([os.path.realpath(project_root), direct_path]) == os.path.realpath(project_root)
        and os.path.isfile(direct_path)
    ):
        return None, direct_path, None

    try:
        rows = workspace.cypher(
            "MATCH (f:file:db) WHERE f.path = $path "
            "RETURN coalesce(f._db_connect, f.db_connect) AS db_connect",
            params={"path": source_path},
        )
        if len(rows) != 1:
            basename = os.path.basename(source_path)
            rows = workspace.cypher(
                "MATCH (f:file:db) WHERE f.name = $name "
                "RETURN coalesce(f._db_connect, f.db_connect) AS db_connect",
                params={"name": basename},
            )
        if len(rows) != 1:
            raise ValueError("not unique")
        db_connect = rows[0].get("db_connect")
        if db_connect is None:
            raise ValueError("not found")
        db_path = getattr(db_connect, "db_path", None)
        if not db_path:
            raise ValueError("not found")
        return db_connect, db_path, None
    except Exception:
        return None, None, f"错误：数据库文件不存在或不唯一: {selector}"


def _normalize_db_selector(selector: str) -> str:
    selector = (selector or "").strip()
    marker = ":db/"
    if marker in selector:
        return selector.split(marker, 1)[0] + ":db"
    return selector


def _register_db_tables(conn: sqlite3.Connection, workspace, source, tables: list[dict]) -> None:
    db_connect, db_path, db_err = _resolve_db_connection(workspace, source.path)
    if db_err:
        raise ValueError(db_err)
    if db_connect is not None:
        path = getattr(db_connect, "db_path", None)
        if not path:
            raise ValueError(f"数据库文件没有可 attach 路径: {source.path}")
        db_path = path
    attach_name = f"db_{len(tables)}"
    conn.execute(f"ATTACH DATABASE ? AS {_quote_ident(attach_name)}", (db_path,))
    rows = conn.execute(
        f"SELECT name FROM {_quote_ident(attach_name)}.sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    db_alias = _safe_table_alias(source.path)
    for (table_name,) in rows:
        safe_name = f"db__{db_alias}__{_safe_table_alias(table_name)}"
        conn.execute(
            f"CREATE TEMP VIEW {_quote_ident(safe_name)} AS "
            f"SELECT * FROM {_quote_ident(attach_name)}.{_quote_ident(table_name)}"
        )
        tables.append({
            "table": safe_name,
            "alias": table_name,
            "source": source.path,
        })


def _register_csv_table(conn: sqlite3.Connection, source, tables: list[dict]) -> None:
    alias = _safe_table_alias(source.path)
    table_name = f"csv__{alias}"
    _load_csv_source_into(conn, source, table_name)
    tables.append({
        "table": table_name,
        "alias": alias,
        "source": source.path,
    })


def _register_json_table(conn: sqlite3.Connection, source, tables: list[dict]) -> None:
    alias = _safe_table_alias(source.path)
    table_name = f"json__{alias}"
    pointer = _load_json_source_into(conn, source, table_name)
    tables.append({
        "table": table_name,
        "alias": alias,
        "source": f"{source.path} {pointer}",
    })


def _query_workspace(workspace, sql: str, limit: int, ref: str) -> str:
    conn = sqlite3.connect(":memory:")
    tables: list[dict] = []
    try:
        for source in _iter_sources(workspace, ("db",)):
            try:
                _register_db_tables(conn, workspace, source, tables)
            except Exception as exc:
                logger.debug("skip workspace DB table registration for %s: %s", source.path, exc)
        for source in _iter_sources(workspace, ("csv", "tsv")):
            try:
                _register_csv_table(conn, source, tables)
            except Exception as exc:
                logger.debug("skip workspace CSV table registration for %s: %s", source.path, exc)
        for source in _iter_sources(workspace, ("json",)):
            try:
                _register_json_table(conn, source, tables)
            except Exception as exc:
                logger.debug("skip workspace JSON table registration for %s: %s", source.path, exc)

        _unique_aliases(tables)
        for table in tables:
            if table.get("use_alias"):
                conn.execute(
                    f"CREATE TEMP VIEW {_quote_ident(table['alias'])} AS "
                    f"SELECT * FROM {_quote_ident(table['table'])}"
                )

        try:
            cols, rows, has_more = _fetch_rows(conn, sql, limit)
            return _format_query_result(cols, rows, has_more, limit)
        except Exception as e:
            hint = _sqlite_error_hint(sql, e)
            hint_line = f"\n修正方向: {hint}" if hint else ""
            return f"SQL 执行错误: {type(e).__name__}: {e}{hint_line}\n{_available_tables_text(ref, tables)}"
    finally:
        conn.close()


def query_command(workspace, sql: str, ref: str = "", limit: int = _DEFAULT_LIMIT) -> str:
    """Execute a read-only SQL query on a database or tabular file ref.

    Args:
        workspace: Workspace 实例
        sql: SQL query (SELECT only)
        ref: DB/CSV/TSV/JSON file ref or "." for workspace query
        limit: Max rows to return
    """
    limit = _DEFAULT_LIMIT if limit is None else max(0, int(limit))
    selector = (ref or "").strip()
    if not selector:
        return "错误：缺少 ref。请传入数据库或表格文件 ref。"

    # 安全校验：只允许只读 SELECT / PRAGMA（包括 WITH ... SELECT）
    stripped = sql.strip()
    if not _is_readonly_sql(stripped):
        return "错误：只允许只读 SELECT / PRAGMA 查询（WITH ... SELECT 也允许）。不允许 INSERT、UPDATE、DELETE 等写操作。"

    if _WRITE_PATTERN.search(stripped):
        return "错误：SQL 中包含写操作关键词，只允许 SELECT 查询。"

    if selector in {".", "*", "workspace"}:
        return _query_workspace(workspace, stripped, limit, selector)

    csv_source, csv_err = _resolve_csv_source(workspace, selector)
    if csv_err:
        return csv_err
    if csv_source is not None:
        return _query_csv_source(stripped, csv_source, limit)

    json_source, json_err = _resolve_json_source(workspace, selector)
    if json_err:
        return json_err
    if json_source is not None:
        return _query_json_source(stripped, json_source, limit)

    db_selector = _normalize_db_selector(selector)
    db_connect, db_path, db_err = _resolve_db_connection(workspace, db_selector)
    if db_err:
        return db_err

    # 执行查询
    conn = None
    try:
        if db_connect is not None:
            conn = db_connect(readonly=True)
        else:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        columns, display_rows, has_more = _fetch_rows(conn, stripped, limit)
    except Exception as e:
        hint = _sqlite_error_hint(stripped, e)
        suffix = f"\n修正方向: {hint}" if hint else ""
        return f"SQL 执行错误: {type(e).__name__}: {e}{suffix}"
    finally:
        if conn is not None:
            conn.close()

    return _format_query_result(columns, display_rows, has_more, limit)
