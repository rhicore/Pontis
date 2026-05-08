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


def query_command(workspace, sql: str, file: str, limit: int = _DEFAULT_LIMIT) -> str:
    """Execute a read-only SQL query on a database file.

    Args:
        workspace: Workspace 实例
        sql: SQL query (SELECT only)
        file: Database file path (relative to project root)
        limit: Max rows to return
    """
    # 安全校验：只允许 SELECT / PRAGMA
    stripped = sql.strip()
    upper = stripped.upper()
    if not (upper.startswith("SELECT") or upper.startswith("PRAGMA")):
        return "错误：只允许 SELECT / PRAGMA 查询。不允许 INSERT、UPDATE、DELETE 等写操作。"

    if _WRITE_PATTERN.search(stripped):
        return "错误：SQL 中包含写操作关键词，只允许 SELECT 查询。"

        # 定位数据库文件（支持绝对路径）
    if os.path.isabs(file):
        db_path = file
    else:
        db_path = workspace.resolve_data_path(file)
    if not os.path.isfile(db_path):
        return f"错误：数据库文件不存在: {file}"

    # 执行查询
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(sql)

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return f"SQL 执行错误: {type(e).__name__}: {e}"

    if not columns:
        return "(查询无结果)"

    total = len(rows)
    display_rows = rows[:limit]

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
        result = result[:_MAX_RESULT_CHARS] + f"\n... (截断，共 {total} 行)"

    if total > limit:
        result += f"\n(共 {total} 行，显示前 {limit} 行)"

    return result
