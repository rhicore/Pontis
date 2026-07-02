"""Deterministic BIRD SQL output checks."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

import sqlglot
from sqlglot import exp


_ROUND_REQUEST_RE = re.compile(
    r"\b(?:round|rounded|decimal|decimals|places|nearest)\b|四舍五入|小数|保留",
    re.IGNORECASE,
)
_COUNT_QUESTION_RE = re.compile(r"\b(?:how many|count|number of)\b", re.IGNORECASE)
_UNIQUE_WORD_RE = re.compile(r"\b(?:distinct|unique|different|separate|individual)\b", re.IGNORECASE)
_TOP_EXTREME_RE = re.compile(
    r"\b(?:top|highest|lowest|most|least|fewest|greatest|biggest|smallest|maximum|minimum|oldest|youngest)\b",
    re.IGNORECASE,
)
_TIE_REQUEST_RE = re.compile(
    r"\b(?:all|every|ties?|tie|same|equal|share|sharing|with the same)\b",
    re.IGNORECASE,
)
_FUZZY_MATCH_REQUEST_RE = re.compile(
    r"\b(?:contain|contains|containing|include|includes|including|starts? with|ends? with|like|pattern|prefix|suffix|substring|part of)\b",
    re.IGNORECASE,
)
_METRIC_OUTPUT_REQUEST_RE = re.compile(
    r"\b(?:how many|how much|count|number of|amount|score|points?|total|sum|average|avg|rate|ratio|percent|percentage|cost|salary|consumption|views?|rank|ranking|mention|state|include|list the score|indicate (?:the )?(?:amount|number|score|count))\b",
    re.IGNORECASE,
)
_REQUESTED_IDS_RE = re.compile(
    r"\b(?:their|the|account|card|set|product|member|client)\s+IDs\b|"
    r"\bIDs\b[^?.;\n]{0,30}\b(?:in your response|as well|along with)\b",
    re.IGNORECASE,
)
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
_PERCENTAGE_QUESTION_RE = re.compile(r"\b(?:percent|percentage|proportion|ratio)\b", re.IGNORECASE)
_PERCENT_ONLY_QUESTION_RE = re.compile(r"\b(?:percent|percentage)\b", re.IGNORECASE)
_FORMULA_EVIDENCE_RE = re.compile(r"=\s*`[^`]+`\s*/\s*`[^`]+`|=\s*[^;\n]+/[^\n]+", re.IGNORECASE)
_DERIVED_METRIC_COLUMN_RE = re.compile(r"(?:percent|percentage|rate|ratio|pct)", re.IGNORECASE)
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
_GROUP_CONCAT_ANONYMOUS_NAMES = {"GROUP_CONCAT"}
_GENERIC_BOOLEAN_LABELS = {"yes", "no", "true", "false"}
_BIRD_STYLE_BOOLEAN_LABELS = {"YES", "NO", "True", "False"}
_METRIC_OUTPUT_NAME_RE = re.compile(
    r"(count|total|sum|avg|average|rate|ratio|percent|score|amount|cost|salary|consumption|points?|views?|rank|cnt)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BirdSqlOutputGuardResult:
    """Deterministic BIRD SQL output guard result."""

    strict: list[str]
    warnings: list[str]
    force: list[str] = field(default_factory=list)


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
            force=_force_messages(),
        )

    prompt_text = f"{question}\n{evidence}"
    strict = _strict_messages(tree, prompt_text)
    warnings = _warning_messages(tree, normalized, prompt_text)
    return BirdSqlOutputGuardResult(strict=_dedupe(strict), warnings=_dedupe(warnings), force=_force_messages())


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


def format_bird_sql_output_guard_force_feedback(force_messages: list[str]) -> str:
    """Format force guard messages that request at least one reflection pass."""

    lines = [
        "SQL 复核要求：请按以下要求重新检查并提交 SQL。",
        "",
    ]
    lines.extend(f"- {message}" for message in force_messages)
    return "\n".join(lines)


def _force_messages() -> list[str]:
    return [
        "SELECT 输出列宁滥勿缺：凡是和题目答案相关的 ID、name、code、number、label、status、time、date、score、amount、count、percent 以及用于区分答案的原始列，都一起输出；多输出题目相关列优先于少输出。"
    ]


def _parse_sql(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except sqlglot.errors.SqlglotError:
        return None


def _strict_messages(tree: exp.Expression, prompt_text: str) -> list[str]:
    strict: list[str] = []
    select = _top_level_select(tree)

    if select and any(_is_select_star_expression(expr) for expr in select.expressions):
        strict.append("使用了 SELECT *；修改方式：逐项列出 question/evidence 要求返回的答案列。")
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
    if _has_left_join(tree):
        warnings.append(
            "候选 SQL 使用了 LEFT JOIN。BIRD dev gold 几乎总是原始等值 INNER JOIN；建议修改方式：除非 question/evidence 明确要求保留无匹配记录、optional 字段、if any/with or without，否则改为 INNER JOIN，并保持原始 key 等值连接。"
        )
    if any(tree.find_all(exp.Union)):
        warnings.append(
            "候选 SQL 使用了 UNION/UNION ALL。BIRD dev gold 几乎总是单一答案表；建议修改方式：除非题面明确要求在多个候选集合或 Max/Min 等分支之间比较并最终选择，否则删除 UNION，把逻辑合并成一张最小答案表。"
        )
    if any(tree.find_all(exp.Except)) or any(tree.find_all(exp.Intersect)):
        warnings.append(
            "候选 SQL 使用了 EXCEPT/INTERSECT 集合运算。BIRD gold 通常用直接 JOIN/WHERE 表达筛选集合；建议修改方式：除非 question/evidence 明确要求集合差集或交集，优先改成单一 SELECT 的 JOIN、WHERE 或 NOT EXISTS/NOT IN 结构，并确认不会改变原始行粒度。"
        )
    if _has_group_concat(tree):
        warnings.append(
            "候选 SQL 使用 GROUP_CONCAT 把多行答案压成一个字符串；建议修改方式：BIRD gold 通常保留多行/多列原始答案，不把 ID、类型、元素或标签聚合成展示字符串。若题面没有明确要求拼接文本，改为直接 SELECT 原始列。"
        )
    if _has_non_strict_string_concat_operator(tree):
        warnings.append(
            "候选 SQL 使用 || 拼接或构造字符串；建议修改方式：BIRD gold 通常返回原始列，不把标签、地址、全名或结构化字段重新拼成展示文本。只有 evidence/schema 明确要求合成键或题面明确要求拼接展示值时才保留。"
        )
    if _has_non_bird_style_boolean_case_label(tree):
        warnings.append(
            "候选 SQL 的 CASE 输出了泛化布尔标签（如 Yes/No、yes/no、true/false）；建议修改方式：BIRD gold 对标签大小写和文本很机械。若题面/evidence 要 YES/NO 或 True/False，按原样输出；若 evidence 定义了 well-finished/NOT well-finished、+/-、Normal/Abnormal 等标签，不要改写成通用 yes/no。"
        )
    if _has_requested_id_missing(tree, prompt_text):
        warnings.append(
            "question/evidence 明确要求返回 IDs，但候选 SELECT 中没有 id 类答案列；建议修改方式：把对应的 id/xxx_id 原始列加入 SELECT。BIRD business 允许多列，不要为了压缩输出而遗漏题面要求的答案列。"
        )
    if _has_early_limit_before_join(sql):
        warnings.append("SQL 在子查询中先 ORDER BY/LIMIT 后再做外层 JOIN 或过滤；建议修改方式：先完成题面范围限定和 JOIN，再排序取 top/bottom/第 N 行。")
    if _has_unrequested_wildcard_like(tree, prompt_text):
        warnings.append(
            "候选 SQL 使用 LIKE 通配符做模糊匹配；建议修改方式：BIRD gold 对题面给出的实体名、地点、格式、语言、代码和时间通常按原始值精确匹配。除非 question/evidence 明确要求 contains/starts with/like/prefix/suffix，否则改成 = 或题面指定的精确条件。"
        )
    if _has_percentage_scalar_count_denominator(tree, prompt_text):
        warnings.append(
            "百分比/比例 SQL 使用了带 COUNT 的标量子查询作为分母或分子；建议修改方式：核查 BIRD gold 的分母是否应与外层同一 join 后行粒度一致。很多 percentage 题应在同一事实表/连接范围内计算 numerator 和 denominator，而不是用独立 base table COUNT。"
        )
    if _has_percentage_count_distinct_case(tree, prompt_text):
        warnings.append(
            "百分比/比例 SQL 使用 COUNT(DISTINCT CASE ...)，会把明细行压成唯一实体口径；建议修改方式：若 question/evidence 没有明确要求唯一实体比例，优先按 BIRD gold 的 join 后原始行粒度计算 COUNT/ SUM(CASE) 分子分母。"
        )
    if _has_percentage_without_times_100(tree, prompt_text):
        warnings.append(
            "question/evidence 要求 percentage/percent，但候选 SQL 没有明显乘以 100；建议修改方式：BIRD gold 的 percentage 通常返回百分数而不是 0-1 小数比例。除非 evidence 明确要求 ratio/proportion 小数，补上 * 100 或等价换算。"
        )
    if _has_formula_metric_shortcut(tree, prompt_text):
        warnings.append(
            "evidence 明确给出了由原始列计算的公式，但候选 SQL 直接返回 percent/rate/ratio 等现成指标列；建议修改方式：BIRD gold 通常机械执行 evidence 公式，使用公式中的原始列做除法/乘法，不要用语义相近的预计算字段替代。"
        )
    if _has_unrequested_derived_raw_answer(tree, prompt_text):
        warnings.append(
            "question/evidence 倾向要求原始 code、grade、tag、label、status 或 time/date 字段，但候选 SELECT 用 SUBSTR/INSTR/CASE/拼接等表达式派生答案；建议修改方式：除非题面明确要求 parse/extract/format/concatenate，否则优先直接返回原始列。"
        )
    if _has_count_with_existence_subquery_without_unique_request(tree, prompt_text):
        warnings.append(
            "计数/数量类 SQL 使用 EXISTS 或 IN 子查询把匹配明细压成“是否存在”；建议修改方式：核查 BIRD 是否要求按 join 后的原始明细行计数。若题面没有明确 unique/distinct，优先改成直接 JOIN 后 COUNT(column) 或 COUNT(*)，不要先按实体去重，也不要改成 SELECT id ... GROUP BY id 的子查询后再 COUNT。"
        )
    if _has_count_distinct_without_unique_request(tree, prompt_text):
        warnings.append(
            "候选 SQL 使用了 COUNT(DISTINCT ...)，但 question/evidence 没有明确要求唯一、不同或去重对象；建议修改方式：核查 BIRD 口径是否应按满足条件的明细行计数，若是则改为 COUNT(column) 或 COUNT(*)。"
        )
    if _has_sum_of_existing_quantity_for_how_many(tree, prompt_text):
        warnings.append(
            "how many 问题中候选 SQL 对已有数量字段使用了 SUM(...)；建议修改方式：先判断 how many 后面问的是对象个数还是对象的已有数量指标。若问对象个数，用 COUNT；若问某对象的已有数量指标，直接返回该数量字段；只有题面或 evidence 明确要求总和时才使用 SUM。"
        )
    if _has_unrequested_top_metric_output(tree, prompt_text):
        warnings.append(
            "top/highest/lowest 类 SQL 在 SELECT 中同时返回了答案对象和排序/聚合指标；建议修改方式：BIRD gold 通常只输出题面询问的最小答案列。若 question/evidence 没有明确要求返回 count、amount、score、total 等指标列，只保留被询问对象，用指标留在 ORDER BY/HAVING 中。"
        )
    if _has_tie_preserving_top_without_limit(tree, prompt_text):
        warnings.append(
            "top/highest/lowest 类 SQL 没有 LIMIT，可能会把所有并列极值都返回；建议修改方式：BIRD gold 多数用 ORDER BY ... LIMIT 1/ N 截断候选。除非题面明确要求 all/tie/same/equal，优先加上题面要求的 LIMIT，并把并列保留逻辑改成排序截断。"
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


def _has_left_join(tree: exp.Expression) -> bool:
    return any((join.args.get("side") or "").upper() == "LEFT" for join in tree.find_all(exp.Join))


def _has_requested_id_missing(tree: exp.Expression, prompt_text: str) -> bool:
    if not _REQUESTED_IDS_RE.search(prompt_text):
        return False
    select = _top_level_select(tree)
    if select is None:
        return False
    if _is_scalar_aggregate_select(select):
        return False
    return not any(_is_id_like_selected_name(name) for name in _selected_output_names(select))


def _selected_output_names(select: exp.Select) -> list[str]:
    names: list[str] = []
    for expression in select.expressions:
        if isinstance(expression, exp.Alias):
            names.append(expression.alias)
        for column in expression.find_all(exp.Column):
            names.append(column.name)
    return [name for name in names if name]


def _is_id_like_selected_name(name: str) -> bool:
    return bool(re.search(r"(^id$|_id$|id$)", name, re.IGNORECASE))


def _is_scalar_aggregate_select(select: exp.Select) -> bool:
    if len(select.expressions) != 1:
        return False
    return any(isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)) for node in select.expressions[0].walk())


def _has_count_with_existence_subquery_without_unique_request(tree: exp.Expression, prompt_text: str) -> bool:
    if not _COUNT_QUESTION_RE.search(prompt_text):
        return False
    if _UNIQUE_WORD_RE.search(prompt_text):
        return False
    if not any(tree.find_all(exp.Count)):
        return False
    for root in _filter_condition_roots(tree):
        if any(root.find_all(exp.Exists)):
            return True
        if any(_is_in_subquery(node) for node in root.find_all(exp.In)):
            return True
    return False


def _is_in_subquery(node: exp.In) -> bool:
    return node.args.get("query") is not None


def _has_unrequested_wildcard_like(tree: exp.Expression, prompt_text: str) -> bool:
    if _FUZZY_MATCH_REQUEST_RE.search(prompt_text):
        return False
    for node in tree.find_all(exp.Like):
        literal = node.expression
        if isinstance(literal, exp.Literal) and literal.is_string:
            value = str(literal.this)
            if "%" in value or "_" in value:
                return True
    return False


def _has_percentage_scalar_count_denominator(tree: exp.Expression, prompt_text: str) -> bool:
    if not _PERCENTAGE_QUESTION_RE.search(prompt_text):
        return False
    return any(tree.find_all(exp.Subquery)) and any(tree.find_all(exp.Count))


def _has_percentage_count_distinct_case(tree: exp.Expression, prompt_text: str) -> bool:
    if not _PERCENTAGE_QUESTION_RE.search(prompt_text):
        return False
    for count in tree.find_all(exp.Count):
        if isinstance(count.this, exp.Distinct) and any(count.find_all(exp.Case)):
            return True
    return False


def _has_percentage_without_times_100(tree: exp.Expression, prompt_text: str) -> bool:
    if not _PERCENT_ONLY_QUESTION_RE.search(prompt_text):
        return False
    return not any(_is_multiply_by_100(node) for node in tree.find_all(exp.Mul))


def _is_multiply_by_100(node: exp.Mul) -> bool:
    return _is_numeric_literal_value(node.this, 100.0) or _is_numeric_literal_value(node.expression, 100.0)


def _is_numeric_literal_value(node: exp.Expression | None, value: float) -> bool:
    if not isinstance(node, exp.Literal) or node.is_string:
        return False
    try:
        return float(str(node.this)) == value
    except ValueError:
        return False


def _has_formula_metric_shortcut(tree: exp.Expression, prompt_text: str) -> bool:
    if not _FORMULA_EVIDENCE_RE.search(prompt_text):
        return False
    if any(tree.find_all(exp.Div)) or any(tree.find_all(exp.Mul)):
        return False
    select = _top_level_select(tree)
    if select is None:
        return False
    for expression in select.expressions:
        expr = expression.this if isinstance(expression, exp.Alias) else expression
        if isinstance(expr, exp.Column) and _DERIVED_METRIC_COLUMN_RE.search(expr.name or ""):
            return True
    return False


def _has_unrequested_derived_raw_answer(tree: exp.Expression, prompt_text: str) -> bool:
    prompt_lower = prompt_text.lower()
    if not re.search(r"\b(?:code|grade|tag|label|status|time|date)\b", prompt_lower):
        return False
    if re.search(r"\b(?:parse|extract|format|formatted|concatenate|split|substring|part of|convert|seconds?|minutes?)\b", prompt_lower):
        return False
    select = _top_level_select(tree)
    if select is None:
        return False
    for expression in select.expressions:
        if any(isinstance(node, (exp.Substring, exp.StrPosition, exp.DPipe, exp.Case)) for node in expression.walk()):
            return True
    return False


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


def _has_group_concat(tree: exp.Expression) -> bool:
    return any(
        isinstance(node, exp.GroupConcat) or _is_named_anonymous(node, _GROUP_CONCAT_ANONYMOUS_NAMES)
        for node in tree.walk()
    )


def _has_non_strict_string_concat_operator(tree: exp.Expression) -> bool:
    return any(tree.find_all(exp.DPipe)) and not _has_space_literal_string_concat(tree)


def _has_non_bird_style_boolean_case_label(tree: exp.Expression) -> bool:
    for case in tree.find_all(exp.Case):
        for literal in case.find_all(exp.Literal):
            if not literal.is_string:
                continue
            label = str(literal.this).strip()
            if label.lower() in _GENERIC_BOOLEAN_LABELS and label not in _BIRD_STYLE_BOOLEAN_LABELS:
                return True
    return False


def _has_unrequested_top_metric_output(tree: exp.Expression, prompt_text: str) -> bool:
    if not _TOP_EXTREME_RE.search(prompt_text):
        return False
    if _METRIC_OUTPUT_REQUEST_RE.search(prompt_text):
        return False
    select = _top_level_select(tree)
    if select is None or len(select.expressions) < 2:
        return False
    has_metric_expression = False
    has_answer_expression = False
    for expression in select.expressions:
        if _is_aggregate_expression(expression):
            has_metric_expression = True
        else:
            has_answer_expression = True
    if not (has_metric_expression and has_answer_expression):
        return False
    return any(_is_metric_like_selected_name(name) for name in _selected_output_names(select))


def _has_tie_preserving_top_without_limit(tree: exp.Expression, prompt_text: str) -> bool:
    if not _TOP_EXTREME_RE.search(prompt_text) or _TIE_REQUEST_RE.search(prompt_text):
        return False
    if any(tree.find_all(exp.Limit)):
        return False
    return any(tree.find_all(exp.Having))


def _is_aggregate_expression(expression: exp.Expression) -> bool:
    return any(isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)) for node in expression.walk())


def _is_metric_like_selected_name(name: str) -> bool:
    return bool(_METRIC_OUTPUT_NAME_RE.search(name or ""))


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
