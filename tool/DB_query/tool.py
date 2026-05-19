"""Query tool — 在数据库上执行只读 SQL 查询。"""
import logging
import os
import re
import sqlite3

logger = logging.getLogger(__name__)

# 匹配非 SELECT 的写操作关键词
_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|ATTACH|DETACH)\b",
    re.IGNORECASE,
)

_DEFAULT_LIMIT = 100
_MAX_RESULT_CHARS = 8000


def _is_readonly_sql(sql: str) -> bool:
    stripped = sql.strip()
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


def query_command(workspace, sql: str, file: str, limit: int = _DEFAULT_LIMIT) -> str:
    """Execute a read-only SQL query on a database file.

    Args:
        workspace: Workspace 实例
        sql: SQL query (SELECT only)
        file: Database file path (relative to project root)
        limit: Max rows to return
    """
    limit = _DEFAULT_LIMIT if limit is None else max(0, int(limit))

    # 安全校验：只允许只读 SELECT / PRAGMA（包括 WITH ... SELECT）
    stripped = sql.strip()
    if not _is_readonly_sql(stripped):
        return "错误：只允许只读 SELECT / PRAGMA 查询（WITH ... SELECT 也允许）。不允许 INSERT、UPDATE、DELETE 等写操作。"

    if _WRITE_PATTERN.search(stripped):
        return "错误：SQL 中包含写操作关键词，只允许 SELECT 查询。"

    if os.path.isabs(file):
        if not os.path.isfile(file):
            return f"错误：数据库文件不存在: {file}"
        db_path = file
        db_connect = None
    else:
        try:
            rows = workspace.cypher(
                "MATCH (f:file:db) WHERE f.path = $path "
                "RETURN coalesce(f._db_connect, f.db_connect) AS db_connect",
                params={"path": file},
            )
            if len(rows) != 1:
                basename = os.path.basename(file)
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
        except Exception:
            return f"错误：数据库文件不存在或不唯一: {file}"

    # 执行查询
    try:
        if db_connect is not None:
            conn = db_connect(readonly=True)
        else:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(sql)

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max(0, limit) + 1)
        has_more = len(rows) > limit
        display_rows = rows[:limit]
        conn.close()
    except Exception as e:
        return f"SQL 执行错误: {type(e).__name__}: {e}"

    if not columns:
        return "(查询无结果)"

    # 格式化输出
    lines = []

    # 表头
    col_widths = [len(c) for c in columns]
    for row in display_rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val if val is not None else "NULL")))

    col_widths = [min(w, 40) for w in col_widths]  # 限制列宽

    header = " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns))
    sep = "-+-".join("-" * w for w in col_widths)
    lines.append(header)
    lines.append(sep)

    for row in display_rows:
        vals = []
        for i, val in enumerate(row):
            s = str(val if val is not None else "NULL")
            if len(s) > 40:
                s = s[:37] + "..."
            vals.append(s.ljust(col_widths[i]))
        lines.append(" | ".join(vals))

    result = "\n".join(lines)

    # 截断过长结果
    if len(result) > _MAX_RESULT_CHARS:
        result = result[:_MAX_RESULT_CHARS] + "\n... (截断)"

    if has_more:
        result += f"\n(结果超过 {limit} 行，仅显示前 {limit} 行)"

    return result
