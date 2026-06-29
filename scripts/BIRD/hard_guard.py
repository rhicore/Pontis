"""Deterministic BIRD SQL output checks."""

from __future__ import annotations

from dataclasses import dataclass
import re

import sqlglot
from sqlglot import exp


_ROUND_REQUEST_RE = re.compile(
    r"\b(?:round|rounded|decimal|decimals|places|nearest)\b|四舍五入|小数|保留",
    re.IGNORECASE,
)
_COUNT_QUESTION_RE = re.compile(r"\b(?:how many|count|number of)\b", re.IGNORECASE)
_UNIQUE_WORD_RE = re.compile(r"\b(?:distinct|unique|different|separate|individual)\b", re.IGNORECASE)
_EXPLICIT_NULL_RE = re.compile(
    r"\b(?:not null|non-null|is not null|has values?|available|exists?|if there are|with values?)\b",
    re.IGNORECASE,
)
_EVIDENCE_SECONDS_COMPARE_RE = re.compile(
    r"\bseconds?\s*(?:<=|>=|<|>|=)\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)
_FORMULA_COUNT_QUESTION_RE = re.compile(
    r"\b(?:how many more|how many percent|percent|percentage|ratio|times|on average|average)\b|比例|百分比|平均",
    re.IGNORECASE,
)
_EXISTING_QUANTITY_COLUMN_RE = re.compile(
    r"(?:num|number|count|enroll|student|taker|amount|total|size|view|favorite|income|population)",
    re.IGNORECASE,
)


def _exp_types(*names: str) -> tuple[type[exp.Expression], ...]:
    return tuple(getattr(exp, name) for name in names if hasattr(exp, name))


_JOIN_FORMATTING_TYPES = _exp_types(
    "Cast",
    "Substring",
    "StrToTime",
    "Date",
    "Lower",
    "Upper",
    "Trim",
    "Replace",
    "Abs",
    "Round",
    "Length",
    "StrPosition",
    "DPipe",
    "Concat",
)
_TEXT_NORMALIZATION_TYPES = _exp_types("Lower", "Upper", "Trim", "Replace")
_CONCAT_TYPES = _exp_types("Concat")
_FORMATTING_ANONYMOUS_NAMES = {"DATETIME", "PRINTF", "CONCAT"}
_TEXT_NORMALIZATION_ANONYMOUS_NAMES = {"PRINTF"}


@dataclass(frozen=True)
class BirdSqlOutputGuardResult:
    """Deterministic BIRD SQL output guard result."""

    strict: list[str]
    warnings: list[str]


def bird_sql_output_guard(
    sql: str,
    *,
    question: str = "",
    evidence: str = "",
) -> BirdSqlOutputGuardResult:
    """Return deterministic BIRD output-style guard findings for a candidate SQL."""

    normalized = sql.strip()
    tree = _parse_sql(normalized)
    if tree is None:
        return BirdSqlOutputGuardResult(
            strict=["SQL 无法按 SQLite 语法解析；修改方式：提交一条可解析的只读 SELECT 或 WITH ... SELECT。"],
            warnings=[],
        )

    prompt_text = f"{question}\n{evidence}"
    strict = _strict_messages(tree, prompt_text)
    warnings = _warning_messages(tree, normalized, prompt_text)
    return BirdSqlOutputGuardResult(strict=_dedupe(strict), warnings=_dedupe(warnings))


def format_bird_sql_output_guard_strict_feedback(violations: list[str]) -> str:
    """Format strict deterministic guard violations as actionable feedback."""

    lines = [
        "SQL 输出严格拦截：候选 SQL 必须按以下要求修改后重新提交。",
        "",
    ]
    lines.extend(f"- {violation}" for violation in violations)
    return "\n".join(lines)


def format_bird_sql_output_guard_warning(warnings: list[str]) -> str:
    """Format one-shot deterministic warnings as actionable feedback."""

    lines = [
        "SQL 输出一次性警告：请按以下要求检查并调整候选 SQL。",
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def _parse_sql(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except sqlglot.errors.SqlglotError:
        return None


def _strict_messages(tree: exp.Expression, prompt_text: str) -> list[str]:
    strict: list[str] = []
    select = _top_level_select(tree)

    if any(tree.find_all(exp.Union)):
        strict.append("使用了 UNION/UNION ALL 拼接多个结果块；修改方式：删除 UNION，把 SQL 改成只返回 question/evidence 要求的同一张答案表。")
    if select and any(_is_select_star_expression(expr) for expr in select.expressions):
        strict.append("使用了 SELECT *；修改方式：逐项列出 question/evidence 要求返回的答案列。")
    if any((join.args.get("side") or "").upper() == "LEFT" for join in tree.find_all(exp.Join)):
        strict.append("使用了 LEFT JOIN；修改方式：改为 INNER JOIN，并保持原有 ON 等值连接条件。")
    if any(tree.find_all(exp.RowNumber)):
        strict.append("使用了 ROW_NUMBER() 截断并列名次；修改方式：ranking/top N within group 题改用 RANK()。")
    if any(isinstance(node, (exp.Coalesce, exp.Nullif)) for node in tree.walk()):
        strict.append("使用了 COALESCE/IFNULL/NULLIF 做防御式 NULL 或除零处理；修改方式：删除这些防御函数，按 question/evidence 公式保留 SQLite 自然结果。")
    if any(_is_text_normalization_expression(node) for node in tree.walk()):
        strict.append("使用了 LOWER/UPPER/TRIM/REPLACE/PRINTF 做文本归一化；修改方式：删除归一化函数，按 question/evidence 文本值和原始字段值直接匹配。")
    if _has_concat_function(tree):
        strict.append("使用了 CONCAT() 拼接字符串；修改方式：不要构造展示字符串，直接返回 question/evidence 要求的原始列。")
    if _has_space_literal_string_concat(tree):
        strict.append("使用了 || 和空格字面量拼接展示文本；修改方式：删除字符串拼接，按 question/evidence 要求返回原始字段；姓名类字段通常保持 first/last 分列。")
    if _uses_unrequested_round(tree, prompt_text):
        strict.append("SQL 使用了 ROUND() 做数值格式化；修改方式：question/evidence 没有要求四舍五入或小数位时，保留 SQLite 自然计算结果。")
    if _has_formatted_join_on(tree):
        strict.append("JOIN ON 对连接键做了 CAST、SUBSTR、拼接、补零或格式化改写；修改方式：改为原始列之间的简单等值连接，例如 table_a.key = table_b.key。")
    if _has_join_on_or_in(tree):
        strict.append("JOIN ON 条件中使用了 OR 或 IN (...)；修改方式：ON 子句只保留一个明确的原始列等值连接，例如 table_a.key = table_b.key，其他筛选条件移到 WHERE 或重新选择唯一 join key。")
    return strict


def _warning_messages(tree: exp.Expression, sql: str, prompt_text: str) -> list[str]:
    warnings: list[str] = []
    warnings.extend(_null_filter_caution_messages(tree, prompt_text))
    if _has_early_limit_before_join(sql):
        warnings.append("SQL 在子查询中先 ORDER BY/LIMIT 后再做外层 JOIN 或过滤；建议修改方式：先完成题面范围限定和 JOIN，再排序取 top/bottom/第 N 行。")
    if _has_count_distinct_without_unique_request(tree, prompt_text):
        warnings.append(
            "候选 SQL 使用了 COUNT(DISTINCT ...)，但 question/evidence 没有明确要求唯一、不同或去重对象；建议修改方式：核查 BIRD 口径是否应按满足条件的明细行计数，若是则改为 COUNT(column) 或 COUNT(*)。"
        )
    if _has_sum_of_existing_quantity_for_how_many(tree, prompt_text):
        warnings.append(
            "how many 问题中候选 SQL 对已有数量字段使用了 SUM(...)；建议修改方式：先判断 how many 后面问的是对象个数还是对象的已有数量指标。若问对象个数，用 COUNT；若问某对象的已有数量指标，直接返回该数量字段；只有题面或 evidence 明确要求总和时才使用 SUM。"
        )
    return warnings


def _top_level_select(tree: exp.Expression) -> exp.Select | None:
    if isinstance(tree, exp.Select):
        return tree
    if isinstance(tree, exp.Union):
        return tree.this if isinstance(tree.this, exp.Select) else None
    return tree.find(exp.Select)


def _is_select_star_expression(node: exp.Expression) -> bool:
    if isinstance(node, exp.Alias):
        return _is_select_star_expression(node.this)
    if isinstance(node, exp.Star):
        return True
    return isinstance(node, exp.Column) and isinstance(node.this, exp.Star)


def _uses_unrequested_round(tree: exp.Expression, prompt_text: str) -> bool:
    return any(isinstance(node, exp.Round) for node in tree.walk()) and not _ROUND_REQUEST_RE.search(prompt_text)


def _has_formatted_join_on(tree: exp.Expression) -> bool:
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause is None:
            continue
        if any(isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)) for node in on_clause.walk()):
            continue
        if any(_is_join_formatting_expression(node) for node in on_clause.walk()):
            return True
    return False


def _has_join_on_or_in(tree: exp.Expression) -> bool:
    return any(
        join.args.get("on") is not None
        and any(isinstance(node, (exp.Or, exp.In)) for node in join.args["on"].walk())
        for join in tree.find_all(exp.Join)
    )


def _has_count_distinct_without_unique_request(tree: exp.Expression, prompt_text: str) -> bool:
    if not _COUNT_QUESTION_RE.search(prompt_text):
        return False
    if _UNIQUE_WORD_RE.search(prompt_text) or _EVIDENCE_SECONDS_COMPARE_RE.search(prompt_text):
        return False
    return any(isinstance(count.this, exp.Distinct) for count in tree.find_all(exp.Count))


def _has_sum_of_existing_quantity_for_how_many(tree: exp.Expression, prompt_text: str) -> bool:
    if not re.search(r"\bhow many\b", prompt_text, re.IGNORECASE):
        return False
    if _FORMULA_COUNT_QUESTION_RE.search(prompt_text):
        return False
    for sum_expr in tree.find_all(exp.Sum):
        if any(_is_existing_quantity_column(column) for column in sum_expr.find_all(exp.Column)):
            return True
    return False


def _is_existing_quantity_column(column: exp.Column) -> bool:
    return bool(_EXISTING_QUANTITY_COLUMN_RE.search(column.name or ""))


def _has_concat_function(tree: exp.Expression) -> bool:
    return any(isinstance(node, _CONCAT_TYPES) or _is_named_anonymous(node, {"CONCAT"}) for node in tree.walk())


def _has_space_literal_string_concat(tree: exp.Expression) -> bool:
    return any(_is_space_literal_dpipe(node) for node in tree.find_all(exp.DPipe))


def _is_space_literal_dpipe(node: exp.DPipe) -> bool:
    return _is_space_literal(node.this) or _is_space_literal(node.expression)


def _is_space_literal(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and node.is_string and str(node.this).strip() == ""


def _null_filter_caution_messages(tree: exp.Expression, prompt_text: str) -> list[str]:
    columns = _null_filter_column_names(tree)
    if not columns:
        return []
    column_text = ", ".join(f"`{name}`" for name in columns[:8])
    if len(columns) > 8:
        column_text += ", ..."
    if _EXPLICIT_NULL_RE.search(prompt_text):
        return [
            f"候选 SQL 对 {column_text} 使用了空值或空串过滤；question/evidence 可能要求有效值。建议修改方式：确认过滤只作用在题面明确要求有效值的字段和候选范围上；如果过滤的是排序字段、汇总行字段或非题面要求字段，删除该过滤。"
        ]
    return [
        f"候选 SQL 对 {column_text} 使用了空值或空串过滤；question/evidence 没有明显要求排除空值。建议修改方式：若这些 NULL 行可能是有效候选记录、汇总记录或排序后的首行，删除该过滤；只有题面明确要求非空、available 或 has value 时才保留。"
    ]


def _null_filter_column_names(tree: exp.Expression) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for root in _filter_condition_roots(tree):
        for name in _null_filter_columns_in(root):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


def _filter_condition_roots(tree: exp.Expression) -> list[exp.Expression]:
    roots: list[exp.Expression] = []
    for where in tree.find_all(exp.Where):
        if where.this is not None:
            roots.append(where.this)
    for having in tree.find_all(exp.Having):
        if having.this is not None:
            roots.append(having.this)
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause is not None:
            roots.append(on_clause)
    return roots


def _null_filter_columns_in(root: exp.Expression) -> list[str]:
    names: list[str] = []
    for node in root.walk():
        if _is_not_null_filter(node):
            col_name = _first_column_name(node)
            if col_name:
                names.append(col_name)
            continue
        if _is_non_empty_string_filter(node):
            col_name = _first_column_name(node)
            if col_name:
                names.append(col_name)
    return names


def _is_not_null_filter(node: exp.Expression) -> bool:
    if not isinstance(node, exp.Not) or not isinstance(node.this, exp.Is):
        return False
    return isinstance(node.this.expression, exp.Null)


def _is_non_empty_string_filter(node: exp.Expression) -> bool:
    if isinstance(node, exp.NEQ):
        return _is_empty_string_literal(node.this) or _is_empty_string_literal(node.expression)
    if isinstance(node, exp.Not) and isinstance(node.this, exp.EQ):
        return _is_empty_string_literal(node.this.this) or _is_empty_string_literal(node.this.expression)
    return False


def _first_column_name(node: exp.Expression) -> str | None:
    column = next(node.find_all(exp.Column), None)
    return column.name if column is not None else None


def _is_empty_string_literal(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and node.is_string and str(node.this) == ""


def _has_early_limit_before_join(sql: str) -> bool:
    return bool(
        re.search(
            r"\(\s*SELECT[\s\S]*?\bORDER\s+BY\b[\s\S]*?\bLIMIT\b[\s\S]*?\)\s+(?:AS\s+)?\w+[\s\S]*?\b(?:JOIN|WHERE)\b",
            sql,
            re.IGNORECASE,
        )
    )


def _is_join_formatting_expression(node: exp.Expression) -> bool:
    return isinstance(node, _JOIN_FORMATTING_TYPES) or _is_named_anonymous(node, _FORMATTING_ANONYMOUS_NAMES)


def _is_text_normalization_expression(node: exp.Expression) -> bool:
    return isinstance(node, _TEXT_NORMALIZATION_TYPES) or _is_named_anonymous(node, _TEXT_NORMALIZATION_ANONYMOUS_NAMES)


def _is_named_anonymous(node: exp.Expression, names: set[str]) -> bool:
    return isinstance(node, exp.Anonymous) and str(node.this).upper() in names


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
