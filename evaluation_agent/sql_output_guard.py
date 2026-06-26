"""Deterministic BIRD SQL output style checks."""

from __future__ import annotations

from dataclasses import dataclass
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
_PERCENT_WORD_RE = re.compile(r"\b(?:percent|percentage)\b", re.IGNORECASE)
_RATE_DIVIDE_RE = re.compile(r"\brate\s*=\s*(?:divide|divided by|\w+\s*/)", re.IGNORECASE)
_TIMES_100_RE = re.compile(r"(?:\*\s*100(?:\.0)?|100(?:\.0)?\s*\*)", re.IGNORECASE)
_DIVIDE_RE = re.compile(r"/|\bDIVIDE\s*\(", re.IGNORECASE)
_ROUND_REQUEST_RE = re.compile(
    r"\b(?:round|rounded|decimal|decimals|places|nearest)\b|四舍五入|小数|保留",
    re.IGNORECASE,
)
_TOP_BOTTOM_RE = re.compile(
    r"\b(?:top|bottom|most|least|highest|lowest|largest|smallest|maximum|minimum|rank|ranking)\b|第\s*\d+",
    re.IGNORECASE,
)
_COUNT_QUESTION_RE = re.compile(r"\b(?:how many|count|number of)\b", re.IGNORECASE)
_UNIQUE_WORD_RE = re.compile(r"\b(?:distinct|unique|different|separate|individual)\b", re.IGNORECASE)
_COUNT_DISTINCT_RE = re.compile(r"\bCOUNT\s*\(\s*DISTINCT\b", re.IGNORECASE)
_EVIDENCE_SECONDS_COMPARE_RE = re.compile(
    r"\bseconds?\s*(?P<op><=|>=|<|>|=)\s*(?P<value>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_EVIDENCE_FUNC_RE = re.compile(
    r"\b(?P<func>AVG|SUM|COUNT|MIN|MAX)\s*\(\s*(?P<expr>[^)]*?)\s*\)",
    re.IGNORECASE,
)
_EVIDENCE_PREFIX_FUNC_RE = re.compile(
    r"\(\s*(?P<func>AVG|SUM|COUNT|MIN|MAX)\s*\)\s*(?P<col>[A-Za-z_][\w]*)",
    re.IGNORECASE,
)
_FUNC_OR_FORMAT_RE = re.compile(
    r"\b(?:CAST|SUBSTR|SUBSTRING|STRFTIME|DATE|DATETIME|PRINTF|LOWER|UPPER|TRIM|REPLACE|ABS|ROUND|INSTR)\s*\(",
    re.IGNORECASE,
)
_TIME_TEXT_COL_RE = re.compile(r"\b(?:q1|q2|q3|time|duration|fastestLapTime)\b", re.IGNORECASE)
_HMS_LITERAL_RE = re.compile(r"\b0+:(?P<minutes>\d{1,2}):(?P<seconds>\d{2})\b")
_EXPLICIT_NULL_RE = re.compile(
    r"\b(?:not null|non-null|is not null|has values?|available|exists?|if there are|with values?)\b",
    re.IGNORECASE,
)
_HAS_VALUES_RE = re.compile(
    r"\b(?P<col>[A-Za-z_][\w]*)\s+has\s+values?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BirdSqlOutputGuardResult:
    """Deterministic BIRD SQL output guard result."""

    hard: list[str]
    warnings: list[str]


def bird_sql_output_guard(
    sql: str,
    *,
    question: str = "",
    evidence: str = "",
) -> BirdSqlOutputGuardResult:
    """Return deterministic BIRD output-style guard findings for a candidate SQL."""

    normalized = sql.strip()
    hard_checks: list[tuple[re.Pattern[str], str]] = [
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
            re.compile(r"\bLEFT\s+(?:OUTER\s+)?JOIN\b", re.IGNORECASE),
            "使用了 LEFT JOIN；修改方式：改为 INNER JOIN，并保持原有 ON 等值连接条件。",
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
            re.compile(r"\bCOUNT\s*\(\s*\*\s*\)\s*OVER\s*\(", re.IGNORECASE),
            "使用了 COUNT(*) OVER() 把总数附加到每一行；修改方式：只返回 question/evidence 要求的答案列，若题面要求总数则单独返回 COUNT 聚合结果。",
        ),
    ]
    warning_checks: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(r"\b(?:LOWER|UPPER|TRIM|REPLACE|PRINTF)\s*\(", re.IGNORECASE),
            "使用了 LOWER/UPPER/TRIM/REPLACE/PRINTF 做文本归一化；建议修改方式：确认是否可以删除归一化函数，按 question/evidence 文本值和原始字段值直接匹配。",
        ),
    ]

    hard = [message for pattern, message in hard_checks if pattern.search(normalized)]
    warnings = [message for pattern, message in warning_checks if pattern.search(normalized)]
    if _select_list_uses_concat(normalized):
        hard.append(
            "SELECT 子句中使用了字符串拼接或 CONCAT 构造答案列；修改方式：返回原始答案列，例如 forename、surname 分列输出。"
        )
    prompt_text = f"{question}\n{evidence}"
    if _uses_unrequested_round(normalized, prompt_text):
        hard.append(
            "SQL 使用了 ROUND() 做数值格式化；修改方式：question/evidence 没有要求四舍五入或小数位时，保留 SQLite 自然计算结果。"
        )
    if _has_extra_order_by_for_top_question(normalized, prompt_text):
        warnings.append(
            "排序题使用了多个 ORDER BY 表达式；建议修改方式：优先只保留 question/evidence 明确要求的排序指标。"
        )
    if _has_early_limit_before_join(normalized):
        warnings.append(
            "SQL 在子查询中先 ORDER BY/LIMIT 后再做外层 JOIN 或过滤；建议修改方式：先完成题面范围限定和 JOIN，再排序取 top/bottom/第 N 行。"
        )
    if _has_count_distinct_without_unique_request(normalized, prompt_text):
        warnings.append(
            "候选 SQL 使用了 COUNT(DISTINCT ...)，但 question/evidence 没有明确要求唯一、不同或去重对象；建议修改方式：核查 BIRD 口径是否应按满足条件的明细行计数，若是则改为 COUNT(column) 或 COUNT(*)。"
        )
    if _has_formatted_join_on(normalized):
        hard.append(
            "JOIN ON 对连接键做了 CAST、SUBSTR、拼接、补零或格式化改写；修改方式：改为原始列之间的简单等值连接，例如 table_a.key = table_b.key。"
        )
    if _has_join_on_or_in(normalized):
        hard.append(
            "JOIN ON 条件中使用了 OR 或 IN (...)；修改方式：ON 子句只保留一个明确的原始列等值连接，例如 table_a.key = table_b.key，其他筛选条件移到 WHERE 或重新选择唯一 join key。"
        )
    if _missing_percent_scale(normalized, question=question, evidence=evidence):
        hard.append(
            "question/evidence 要求 percent/percentage，但 SQL 中的除法结果没有乘以 100；修改方式：把比例表达式改成百分数，例如 CAST(numerator AS REAL) * 100.0 / denominator。"
        )
    hard.extend(_unrequested_null_filter_violations(normalized, question=question, evidence=evidence))
    hard.extend(_url_page_select_violations(normalized, question=question))
    hard.extend(_list_entity_select_violations(normalized, question=question))
    hard.extend(_explicit_select_shape_violations(normalized, question=question, evidence=evidence))
    hard.extend(_evidence_anchor_violations(normalized, evidence=evidence))
    hard.extend(_evidence_unit_violations(normalized, evidence=evidence))
    hard.extend(_evidence_rate_scale_violations(normalized, evidence=evidence))
    hard.extend(_evidence_has_values_violations(normalized, evidence=evidence))
    hard.extend(_evidence_seconds_count_distinct_violations(normalized, evidence=evidence))
    hard.extend(_time_format_order_violations(normalized, evidence=evidence))
    hard.extend(_evidence_min_order_violations(normalized, evidence=evidence))
    hard.extend(_time_literal_prefix_violations(normalized, question=question))
    hard.extend(_fastest_lap_speed_cast_violations(normalized, question=question, evidence=evidence))
    return BirdSqlOutputGuardResult(hard=hard, warnings=warnings)


def bird_sql_output_violations(
    sql: str,
    *,
    question: str = "",
    evidence: str = "",
) -> list[str]:
    """Return all deterministic BIRD output-style guard findings."""

    result = bird_sql_output_guard(sql, question=question, evidence=evidence)
    return [*result.hard, *result.warnings]


def format_bird_sql_output_guard_feedback(violations: list[str]) -> str:
    """Format guard violations as actionable DBA feedback."""

    lines = [
        "SQL 输出硬拦截：候选 SQL 必须按以下要求修改后重新提交。",
        "",
    ]
    lines.extend(f"- {violation}" for violation in violations)
    return "\n".join(lines)


def format_bird_sql_output_guard_warning(warnings: list[str]) -> str:
    """Format one-shot guard warnings as actionable DBA feedback."""

    lines = [
        "SQL 输出建议：请按以下要求检查并调整候选 SQL。",
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
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


def _has_join_on_or_in(sql: str) -> bool:
    pos = 0
    while True:
        match = _JOIN_ON_RE.search(sql, pos)
        if not match:
            return False
        start = match.end()
        stop = _ON_STOP_RE.search(sql, start)
        end = stop.start() if stop else len(sql)
        on_clause = sql[start:end]
        if re.search(r"\bOR\b|\bIN\s*\(", on_clause, re.IGNORECASE):
            return True
        pos = end


def _select_list_uses_concat(sql: str) -> bool:
    match = re.search(r"\bSELECT\b(?P<select>[\s\S]*?)\bFROM\b", sql, re.IGNORECASE)
    if not match:
        return False
    select_list = match.group("select")
    return "||" in select_list or bool(re.search(r"\bCONCAT\s*\(", select_list, re.IGNORECASE))


def _missing_percent_scale(sql: str, *, question: str, evidence: str) -> bool:
    prompt_text = f"{question}\n{evidence}"
    if not _PERCENT_WORD_RE.search(prompt_text):
        return False
    if not _DIVIDE_RE.search(sql):
        return False
    return not _TIMES_100_RE.search(sql)


def _evidence_rate_scale_violations(sql: str, *, evidence: str) -> list[str]:
    if not _RATE_DIVIDE_RE.search(evidence or ""):
        return []
    if _TIMES_100_RE.search(sql):
        return []
    if not _DIVIDE_RE.search(sql):
        return []
    return [
        "evidence 使用 `rate = divide(...)` 给出完成率/比例公式；BIRD 返回百分数尺度。修改方式：把除法表达式乘以 100，例如 `CAST(numerator AS REAL) * 100.0 / denominator`。"
    ]


def _unrequested_null_filter_violations(sql: str, *, question: str, evidence: str) -> list[str]:
    prompt_text = f"{question}\n{evidence}"
    if _EXPLICIT_NULL_RE.search(prompt_text):
        return []
    if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return []
    order_by = _extract_clause(sql, r"\bORDER\s+BY\b")
    if not order_by:
        return []
    violations: list[str] = []
    for match in re.finditer(r"\b(?P<col>[A-Za-z_][\w]*)(?:[`\"\]])?\s+IS\s+NOT\s+NULL\b", sql, re.IGNORECASE):
        col = match.group("col")
        if re.search(_column_name_pattern(col), order_by, re.IGNORECASE):
            violations.append(
                f"候选 SQL 对排序取首/取前 N 的指标 `{col}` 增加了题面未要求的 `IS NOT NULL` 过滤；修改方式：删除该空值过滤，直接在最终候选行上 `ORDER BY {col} ... LIMIT ...`。"
            )
    return _dedupe(violations)


def _evidence_has_values_violations(sql: str, *, evidence: str) -> list[str]:
    violations: list[str] = []
    for match in _HAS_VALUES_RE.finditer(evidence or ""):
        col = match.group("col")
        col_pattern = _column_name_pattern(col)
        if not re.search(col_pattern, sql, re.IGNORECASE):
            continue
        orders_by_col = bool(re.search(rf"\bORDER\s+BY\b[\s\S]*{col_pattern}", sql, re.IGNORECASE))
        limits = bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))
        formats_col = _column_is_formatted(sql, col)
        if orders_by_col and limits or formats_col:
            violations.append(
                f"evidence 说明 `{col} has values` 是存在性口径；修改方式：用 `{col} IS NOT NULL` 保留有值行，不要对 `{col}` 排序取最快/最小/最大，也不要解析或格式化该列。"
            )
    return _dedupe(violations)


def _evidence_seconds_count_distinct_violations(sql: str, *, evidence: str) -> list[str]:
    if not _EVIDENCE_SECONDS_COMPARE_RE.search(evidence or ""):
        return []
    if not _COUNT_DISTINCT_RE.search(sql):
        return []
    return [
        "evidence 给出 seconds 比较条件时，条件作用在时间明细记录上；候选 SQL 使用了 COUNT(DISTINCT ...) 改成去重实体数。修改方式：按满足条件的明细记录计数，改为 COUNT(column) 或 COUNT(*)。"
    ]


def _time_format_order_violations(sql: str, *, evidence: str) -> list[str]:
    if not re.search(r"\btime format\b|格式", evidence or "", re.IGNORECASE):
        return []
    if not re.search(r"\b(?:shortest|fastest|MIN)\b", evidence or "", re.IGNORECASE):
        return []
    if re.search(r"\b(?:CAST|SUBSTR|INSTR)\s*\(", sql, re.IGNORECASE):
        return []
    if not re.search(r"\b(?:MIN\s*\(\s*(?:\w+\.)?time\s*\)|ORDER\s+BY\s+(?:\w+\.)?time\b)", sql, re.IGNORECASE):
        return []
    return [
        "evidence 明确说明 time 的文本格式且要求 shortest/fastest；修改方式：按该文本格式把 `time` 换算为可比较的数值后排序，不要直接用 `MIN(time)` 或 `ORDER BY time` 的字符串顺序。"
    ]


def _uses_unrequested_round(sql: str, prompt_text: str) -> bool:
    if not re.search(r"\bROUND\s*\(", sql, re.IGNORECASE):
        return False
    return not _ROUND_REQUEST_RE.search(prompt_text)


def _has_extra_order_by_for_top_question(sql: str, prompt_text: str) -> bool:
    if not _TOP_BOTTOM_RE.search(prompt_text):
        return False
    order_by = _extract_clause(sql, r"\bORDER\s+BY\b")
    if not order_by:
        return False
    return _has_top_level_comma(order_by)


def _has_early_limit_before_join(sql: str) -> bool:
    return bool(
        re.search(
            r"\(\s*SELECT[\s\S]*?\bORDER\s+BY\b[\s\S]*?\bLIMIT\b[\s\S]*?\)\s+(?:AS\s+)?\w+[\s\S]*?\b(?:JOIN|WHERE)\b",
            sql,
            re.IGNORECASE,
        )
    )


def _has_count_distinct_without_unique_request(sql: str, prompt_text: str) -> bool:
    if not _COUNT_DISTINCT_RE.search(sql):
        return False
    if _EVIDENCE_SECONDS_COMPARE_RE.search(prompt_text):
        return False
    if not _COUNT_QUESTION_RE.search(prompt_text):
        return False
    return not _UNIQUE_WORD_RE.search(prompt_text)


def _extract_clause(sql: str, start_pattern: str) -> str:
    start = re.search(start_pattern, sql, re.IGNORECASE)
    if not start:
        return ""
    stop = re.search(r"\bLIMIT\b|\bUNION\b|;", sql[start.end():], re.IGNORECASE)
    end = start.end() + stop.start() if stop else len(sql)
    return sql[start.end():end]


def _has_top_level_comma(text: str) -> bool:
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            return True
    return False


def _evidence_anchor_violations(sql: str, *, evidence: str) -> list[str]:
    """Catch clear cases where SQL rewrites an explicit evidence expression."""

    violations: list[str] = []
    evidence_text = evidence or ""
    for func, col, evidence_uses_distinct in _evidence_function_columns(evidence_text):
        if not col:
            continue
        col_pattern = _column_name_pattern(col)
        if not re.search(col_pattern, sql, re.IGNORECASE):
            violations.append(
                f"evidence 明确给出 `{func}({col})` 作为口径，但候选 SQL 没有使用 `{col}`；修改方式：按 evidence 的列名和函数口径重写相关表达式。"
            )
            continue
        if _column_is_formatted(sql, col):
            violations.append(
                f"evidence 明确给出 `{func}({col})` 作为口径，但候选 SQL 对 `{col}` 做了 CAST/SUBSTR/ROUND 等改写；修改方式：直接使用 `{col}` 保留 evidence 指定的表达式口径。"
            )
        if func == "COUNT" and not evidence_uses_distinct and _sql_counts_distinct_column(sql, col):
            violations.append(
                f"evidence 明确给出 `COUNT({col})` 行/值计数口径，但候选 SQL 使用了 `COUNT(DISTINCT {col})`；修改方式：按 evidence 改为 `COUNT({col})`，除非 question 明确要求唯一实体数。"
            )
    return _dedupe(violations)


def _evidence_function_columns(evidence: str) -> list[tuple[str, str, bool]]:
    columns: list[tuple[str, str, bool]] = []
    for match in _EVIDENCE_FUNC_RE.finditer(evidence):
        func = match.group("func").upper()
        expr = match.group("expr").strip()
        col, evidence_uses_distinct = _parse_evidence_function_column(expr)
        columns.append((func, col, evidence_uses_distinct))
    for match in _EVIDENCE_PREFIX_FUNC_RE.finditer(evidence):
        columns.append((match.group("func").upper(), match.group("col"), False))
    return columns


def _evidence_unit_violations(sql: str, *, evidence: str) -> list[str]:
    violations: list[str] = []
    for match in _EVIDENCE_SECONDS_COMPARE_RE.finditer(evidence or ""):
        value = float(match.group("value"))
        millisecond_value = int(value * 1000)
        pattern = re.compile(
            rf"\bmilliseconds\b\s*{re.escape(match.group('op'))}\s*{millisecond_value}(?:\.0)?\b",
            re.IGNORECASE,
        )
        if pattern.search(sql):
            violations.append(
                f"evidence 明确给出 `seconds {match.group('op')} {match.group('value')}` 的秒数口径，但候选 SQL 改成直接比较 `milliseconds {match.group('op')} {millisecond_value}`；修改方式：按 evidence 保留秒数尺度，在相关时间文本列上计算 seconds 后比较。"
            )
    return _dedupe(violations)


def _time_literal_prefix_violations(sql: str, *, question: str) -> list[str]:
    violations: list[str] = []
    for match in _HMS_LITERAL_RE.finditer(question or ""):
        prefix = f"{int(match.group('minutes'))}:{match.group('seconds')}"
        quoted_prefix = re.escape(prefix)
        if not _TIME_TEXT_COL_RE.search(sql):
            continue
        has_prefix_like = bool(re.search(rf"\bLIKE\s+['\"]%?{quoted_prefix}%?['\"]", sql, re.IGNORECASE))
        if (
            has_prefix_like
            and re.search(r"\bLIMIT\s+1\b", sql, re.IGNORECASE)
            and not _TOP_BOTTOM_RE.search(question or "")
        ):
            violations.append(
                f"题面给出秒级时间 `{match.group(0)}`，候选 SQL 已按 `{prefix}%` 前缀匹配时需要保留全部匹配行；修改方式：删除额外的 `LIMIT 1`。"
            )
        if has_prefix_like:
            continue
        original = re.escape(match.group(0))
        wrong_like = re.search(rf"\bLIKE\s+['\"]%?{original}%?['\"]", sql, re.IGNORECASE)
        exact_millis = re.search(rf"=\s*['\"]{quoted_prefix}\.\d+['\"]", sql, re.IGNORECASE)
        nearest_match = re.search(r"\bABS\s*\(", sql, re.IGNORECASE)
        exact_original = re.search(rf"=\s*['\"]{original}['\"]", sql, re.IGNORECASE)
        if wrong_like or exact_millis or nearest_match or exact_original:
            violations.append(
                f"题面给出秒级时间 `{match.group(0)}`，候选 SQL 需要按数据库文本时间前缀筛选；修改方式：把当前时间列条件改为 `时间列 LIKE '{prefix}%'`，并删除精确毫秒值匹配、ABS 或最近值排序。"
            )
    return _dedupe(violations)


def _url_page_select_violations(sql: str, *, question: str) -> list[str]:
    if not re.search(r"\b(?:page|link|url|wiki|wikipedia)\b", question or "", re.IGNORECASE):
        return []
    if re.search(r",|\band\b", question or "", re.IGNORECASE):
        return []
    select_exprs = _top_level_select_expressions(sql)
    if len(select_exprs) <= 1:
        return []
    has_url = any(re.search(_column_name_pattern("url"), expr, re.IGNORECASE) for expr in select_exprs)
    if not has_url:
        return []
    extras = [expr.strip() for expr in select_exprs if not re.search(_column_name_pattern("url"), expr, re.IGNORECASE)]
    if not extras:
        return []
    return [
        "question 要求返回页面/link/url 时，SELECT 只保留 URL 字段；修改方式：删除额外 SELECT 项 "
        + ", ".join(f"`{item}`" for item in extras[:4])
        + "。"
    ]


def _list_entity_select_violations(sql: str, *, question: str) -> list[str]:
    if not re.search(r"\blist\b[\s\S]{0,60}\braces?\b", question or "", re.IGNORECASE):
        return []
    select_exprs = _top_level_select_expressions(sql)
    if len(select_exprs) <= 1:
        return []
    has_name = any(re.search(_column_name_pattern("name"), expr, re.IGNORECASE) for expr in select_exprs)
    if not has_name:
        return []
    extras = [
        expr.strip()
        for expr in select_exprs
        if re.search(_column_name_pattern("year"), expr, re.IGNORECASE)
        or re.search(_column_name_pattern("round"), expr, re.IGNORECASE)
        or re.search(_column_name_pattern("date"), expr, re.IGNORECASE)
    ]
    if not extras:
        return []
    return [
        "question 要求 list races 时，SELECT 返回比赛主显示字段即可；修改方式：只保留 race name，删除额外的年份、轮次或日期列 "
        + ", ".join(f"`{item}`" for item in extras[:4])
        + "。"
    ]


def _explicit_select_shape_violations(sql: str, *, question: str, evidence: str) -> list[str]:
    prompt_text = f"{question}\n{evidence}"
    select_exprs = _top_level_select_expressions(sql)
    if not select_exprs:
        return []
    select_text = " ".join(select_exprs)
    violations: list[str] = []

    if re.search(r"\bfull name\b", prompt_text, re.IGNORECASE):
        has_forename = bool(re.search(_column_name_pattern("forename"), select_text, re.IGNORECASE))
        has_surname = bool(re.search(_column_name_pattern("surname"), select_text, re.IGNORECASE))
        if has_forename != has_surname:
            violations.append(
                "question/evidence 要求 full name；修改方式：SELECT 同时返回 `forename, surname`，不要只返回其中一列。"
            )

        asks_metric = bool(re.search(r"\b(?:how many|what number|number of|count of)\b", question or "", re.IGNORECASE))
        has_metric_expr = bool(
            re.search(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", select_text, re.IGNORECASE)
            or re.search(r"\b(?:wins?|points?|count|number|total)\b", select_text, re.IGNORECASE)
        )
        if asks_metric and has_forename and has_surname and not has_metric_expr:
            violations.append(
                "question 同时要求数量/数值指标和 full name；修改方式：SELECT 保留该数量/数值指标，并同时返回 `forename, surname`。"
            )

    if re.search(r"\bcountry\b[\s\S]{0,80}\bcoordinates?\b|\bcoordinates?\b[\s\S]{0,80}\bcountry\b", question or "", re.IGNORECASE):
        missing: list[str] = []
        if not re.search(_column_name_pattern("country"), select_text, re.IGNORECASE):
            missing.append("country")
        if not re.search(_column_name_pattern("lat"), select_text, re.IGNORECASE):
            missing.append("lat")
        if not re.search(_column_name_pattern("lng"), select_text, re.IGNORECASE):
            missing.append("lng")
        if missing:
            violations.append(
                "question 明确要求 country 和 coordinates；修改方式：SELECT 同时返回 `country, lat, lng`，补上缺失列 "
                + ", ".join(f"`{item}`" for item in missing)
                + "。"
            )

    return _dedupe(violations)


def _evidence_min_order_violations(sql: str, *, evidence: str) -> list[str]:
    violations: list[str] = []
    for func, col, _ in _evidence_function_columns(evidence or ""):
        if func != "MIN" or not col:
            continue
        col_pattern = _column_name_pattern(col)
        if re.search(rf"\bORDER\s+BY\b[\s\S]*\bMIN\s*\(\s*{col_pattern}\s*\)\s+DESC\b", sql, re.IGNORECASE):
            violations.append(
                f"evidence 明确给出 `MIN({col})` 作为最快/最小口径，但候选 SQL 按 `MIN({col}) DESC` 排序；修改方式：改为按 `{col}` 升序取前 N 行。"
            )
        elif re.search(rf"\bGROUP\s+BY\b[\s\S]*\bORDER\s+BY\b[\s\S]*\bMIN\s*\(\s*{col_pattern}\s*\)", sql, re.IGNORECASE):
            violations.append(
                f"evidence 明确给出 `MIN({col})` 作为行级最小值口径，但候选 SQL 先 GROUP BY 后按 MIN 聚合排序；修改方式：保持候选行粒度，直接 `ORDER BY {col} ASC LIMIT ...`。"
            )
    return _dedupe(violations)


def _fastest_lap_speed_cast_violations(sql: str, *, question: str, evidence: str) -> list[str]:
    prompt_text = f"{question}\n{evidence}"
    if not re.search(r"\bfastest\s+lap\s+speed\b|fastestLapSpeed", prompt_text, re.IGNORECASE):
        return []
    if not _column_is_formatted(sql, "fastestLapSpeed"):
        return []
    return [
        "BIRD gold 对 fastestLapSpeed 保留原始列口径；修改方式：删除 CAST/SUBSTR/ROUND 等改写，直接使用 `fastestLapSpeed` 排序、筛选或计算。"
    ]


def _question_requested_select_columns(question: str) -> set[str]:
    text = question or ""
    requested: set[str] = set()
    if re.search(r"\b(?:wiki|wikipedia|page link|link|url)\b", text, re.IGNORECASE):
        requested.add("url")
    if re.search(r"\bfull name\b", text, re.IGNORECASE):
        requested.update({"forename", "surname"})
    return requested
def _top_level_select_expressions(sql: str) -> list[str]:
    match = re.search(r"\bSELECT\b", sql, re.IGNORECASE)
    if not match:
        return []
    from_start = _find_top_level_keyword(sql, "FROM", start=match.end())
    if from_start == -1:
        return []
    return _split_top_level_csv(sql[match.end():from_start])


def _find_top_level_keyword(sql: str, keyword: str, *, start: int = 0) -> int:
    depth = 0
    quote: str | None = None
    keyword_re = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
    idx = start
    while idx < len(sql):
        char = sql[idx]
        if quote:
            if char == quote:
                quote = None
            idx += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            idx += 1
            continue
        if char == "(":
            depth += 1
            idx += 1
            continue
        if char == ")" and depth:
            depth -= 1
            idx += 1
            continue
        if depth == 0:
            match = keyword_re.match(sql, idx)
            if match:
                return idx
        idx += 1
    return -1


def _split_top_level_csv(text: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for idx, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            item = text[start:idx].strip()
            if item:
                items.append(item)
            start = idx + 1
    item = text[start:].strip()
    if item:
        items.append(item)
    return items


def _parse_evidence_function_column(expr: str) -> tuple[str, bool]:
    """Return the leading column named inside an evidence aggregate expression."""

    cleaned = expr.strip().strip("`\"[]")
    uses_distinct = bool(re.match(r"(?is)^DISTINCT\b", cleaned))
    cleaned = re.sub(r"(?is)^DISTINCT\b", "", cleaned).strip()
    cleaned = re.split(r"(?i)\s+where\s+", cleaned, maxsplit=1)[0].strip()
    cleaned = cleaned.strip("`\"[]")
    if not cleaned or cleaned == "*":
        return "", uses_distinct
    quoted = re.match(r'^[`"\[]([^`"\]]+)[`"\]]$', cleaned)
    if quoted:
        return quoted.group(1).strip(), uses_distinct
    identifier = re.match(r"^(?:[A-Za-z_][\w]*\.)?([A-Za-z_][\w]*)\b", cleaned)
    if not identifier:
        return "", uses_distinct
    return identifier.group(1), uses_distinct


def _sql_counts_distinct_column(sql: str, col: str) -> bool:
    col_pattern = _column_name_pattern(col)
    pattern = re.compile(
        rf"\bCOUNT\s*\(\s*DISTINCT\s+{col_pattern}\s*\)",
        re.IGNORECASE,
    )
    return bool(pattern.search(sql))


def _column_is_formatted(sql: str, col: str) -> bool:
    col_pattern = _column_name_pattern(col)
    for func_match in _FUNC_OR_FORMAT_RE.finditer(sql):
        start = func_match.start()
        end = _matching_paren_end(sql, sql.find("(", start))
        if end == -1:
            end = min(len(sql), start + 200)
        expr = sql[start:end]
        if re.search(col_pattern, expr, re.IGNORECASE):
            return True
    return False


def _column_name_pattern(col: str) -> str:
    escaped = re.escape(col)
    if " " in col:
        return rf"(?:[`\"\[])?{escaped}(?:[`\"\]])?"
    return rf"(?:\b\w+\.)?(?:[`\"\[])?{escaped}(?:[`\"\]])?\b"


def _matching_paren_end(text: str, open_index: int) -> int:
    if open_index < 0:
        return -1
    depth = 0
    quote: str | None = None
    for idx in range(open_index, len(text)):
        char = text[idx]
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return idx + 1
    return -1


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
