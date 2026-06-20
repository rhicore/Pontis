"""Deterministic BIRD SQL output style checks."""

from __future__ import annotations

import re


_JOIN_ON_RE = re.compile(r"\bJOIN\b[\s\S]*?\bON\b", re.IGNORECASE)
_ON_STOP_RE = re.compile(
    r"\b(?:LEFT|RIGHT|FULL|INNER|OUTER|CROSS|NATURAL)?\s*JOIN\b"
    r"|\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|;",
    re.IGNORECASE,
)
_FORMATTED_ON_RE = re.compile(
    r"\b(?:CAST|SUBSTR|SUBSTRING|STRFTIME|DATE|DATETIME|PRINTF|CONCAT|LOWER|UPPER|TRIM|REPLACE|ABS|ROUND|LENGTH|INSTR)\s*\("
    r"|\|\|",
    re.IGNORECASE,
)


def bird_sql_output_violations(sql: str) -> list[str]:
    """Return deterministic BIRD output-style violations for a candidate SQL."""

    normalized = sql.strip()
    checks: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(r"\bLEFT\s+(?:OUTER\s+)?JOIN\b", re.IGNORECASE),
            "使用了 LEFT JOIN；修改方式：改为 INNER JOIN，并保持原有 ON 等值连接条件。",
        ),
        (
            re.compile(r"\bUNION\s+ALL\b", re.IGNORECASE),
            "使用了 UNION ALL 拼接多个结果块；修改方式：删除 UNION ALL，把 SQL 改成只返回题面要求的同一张答案表。",
        ),
        (
            re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?['\"]", re.IGNORECASE),
            "使用了 SELECT 字符串常量构造 metric/value/report 形式答案表；修改方式：删除字符串常量列，直接 SELECT 题面要求的答案列。",
        ),
        (
            re.compile(r"\bSELECT\s+\*", re.IGNORECASE),
            "使用了 SELECT *；修改方式：逐项列出 question/evidence 要求返回的答案列。",
        ),
        (
            re.compile(r"\bOFFSET\b", re.IGNORECASE),
            "使用了 OFFSET；修改方式：第 N、top N、bottom N 或连续位置题改用 LIMIT offset, count。",
        ),
        (
            re.compile(r"\b(?:ORDER\s+BY|GROUP\s+BY)\s+\d+\b", re.IGNORECASE),
            "使用了列序号排序或分组；修改方式：ORDER BY/GROUP BY 写明确表达式或列名。",
        ),
        (
            re.compile(r"\bROW_NUMBER\s*\(", re.IGNORECASE),
            "使用了 ROW_NUMBER() 截断并列名次；修改方式：ranking/top N within group 题改用 RANK()。",
        ),
        (
            re.compile(r"\b(?:COALESCE|IFNULL|NULLIF)\s*\(", re.IGNORECASE),
            "使用了 COALESCE/IFNULL/NULLIF 做防御式 NULL 或除零处理；修改方式：删除这些防御函数，按 question/evidence 公式保留 SQLite 自然结果。",
        ),
        (
            re.compile(r"\b(?:LOWER|UPPER|TRIM|REPLACE|PRINTF)\s*\(", re.IGNORECASE),
            "使用了 LOWER/UPPER/TRIM/REPLACE/PRINTF 做文本归一化；修改方式：删除归一化函数，按 question/evidence 文本值和原始字段值直接匹配。",
        ),
    ]

    violations = [message for pattern, message in checks if pattern.search(normalized)]
    if _has_formatted_join_on(normalized):
        violations.append(
            "JOIN ON 对连接键做了 CAST、SUBSTR、拼接、补零或格式化改写；修改方式：改为原始列之间的简单等值连接，例如 table_a.key = table_b.key。"
        )
    return violations


def format_bird_sql_output_guard_feedback(violations: list[str]) -> str:
    """Format guard violations as actionable DBA feedback."""

    lines = [
        "SQL 输出硬拦截：候选 SQL 使用了 BIRD gold SQL 中禁用的写法，必须修改后重新提交。",
        "",
    ]
    lines.extend(f"- {violation}" for violation in violations)
    return "\n".join(lines)


def _has_formatted_join_on(sql: str) -> bool:
    pos = 0
    while True:
        match = _JOIN_ON_RE.search(sql, pos)
        if not match:
            return False
        start = match.end()
        stop = _ON_STOP_RE.search(sql, start)
        end = stop.start() if stop else len(sql)
        on_clause = sql[start:end]
        if _FORMATTED_ON_RE.search(on_clause):
            return True
        pos = end
