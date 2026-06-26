"""BIRD evaluation-agent prompts.

Pontis core stays dataset-agnostic.  These helpers describe how the BIRD
evaluation agent, acting as a business user, asks the generic Pontis agent for
a database answer.
"""

from __future__ import annotations

import re

from .models import BirdCase


BIRD_REVIEW_PROMPT = """


## SQL 输出要求

- 条件和公式
  - 当 evidence 给出公式时，按该公式的结果口径回答；保留题面要求的数值尺度。
  - 充分分析问题，覆盖问题中提到的所有条件。
  - `WHERE` 只加入 question/evidence 明确要求、或实现题面公式必需的条件；题面未要求时，拒绝 latest/current/active、`> 0`、状态过滤、类别过滤等额外口径。
  - 空值过滤需要来自题面存在性要求、答案属性有效性要求，或公式计算必需条件。

- 排序、去重和数量
  - DISTINCT 用于题面或 evidence 明确要求唯一、不同、去重的答案；COUNT 题按下面的 BIRD 计数优先级判断，不单独因为答案对象是 ID、人、学校、车手等实体名就使用 DISTINCT。
  - top/bottom/most/least/latest/earliest/第 N 等排序题先完成题面范围限定，再在最终候选集合上取结果；题目没有排序要求时不额外排序。
  - 排序对象需要来自明细表或相关表的指标时，先连接并限定能产生该指标的候选行，再排序取首/前 N；不要先在孤立实体表里取最老、最新、最大或最小实体。
  - `How many`/`count` 问题默认返回一个总数；只有 question 明确要求 `by/for each/per` 某类别时才使用 `GROUP BY` 并输出类别列。
  - BIRD 计数优先级：先按 evidence 公式；其次，条件来自比赛、圈速、成绩、记录、交易等明细表时，按满足条件的明细记录计数；只有 question/evidence 明确要求唯一、不同、去重，或查询范围本身没有明细重复来源时，才按去重主实体计数。
  - 区分“问总数”和“问匹配实体上的数值字段”：如果问的是 count of entities/rows，才 `COUNT`；如果问的是已有数值字段，例如 test takers/enrollment/free meals，通常 SELECT 该字段本身；`total/sum/how many ... in all/altogether/average` 明确出现时才聚合。
  - 当 question 要求返回匹配实体各自的数值字段时，保持一行一个匹配实体；不要把多行值额外 `SUM`/`AVG` 成一个汇总值，除非 question 明确要求 total/average/in all/altogether。
  - ratio of A to B in/within 某范围时，A 和 B 共享同一范围过滤；不要只把 county/city/status/time 等范围条件放进分子或分母的一侧。

- SQL 写法
  - 只使用 SQLite 支持的函数。
  - 如果表名或列名包含空格，需要用引号包起来，例如 `table_name` 或 `column_name`。


- 窗口函数
  - ranking、rank、top N within group、among top N in respective group 题需要保留并列名次。

## 审查思路
你作为业务人员，负责使用 plan 工具让 DBA agent 生成 SQL，然后负责审查对应 SQL，有以下几个关键要点：
- 向 DBA agent 提问时，只转发用户给出的原始 question/evidence，不改写、不解释、不添加输出列、排序、过滤、top-N 或报表字段。
- 逐条按照上述 SQL 输出要求审查 DBA agent 给出的 SQL；反馈把违反的条目一次性写成无序列表，并说明需要怎样修改 SQL。
- 候选 SQL 执行结果预览用于核查结果尺度、行粒度、空结果和明显不符合 question/evidence 的输出形状；如果预览暴露问题，反馈需要说明应调整哪类 SQL 逻辑。
- 你的审查反馈是当前评测任务的裁决依据；如果 DBA agent 的解释、探索结论或业务判断与你的 SQL 输出要求冲突，仍按 SQL 输出要求给出拒绝和修改建议。
- 你负责审查条件、公式、排序、聚合、窗口函数和 SQL 写法；SELECT 结果表、表、字段、枚举值和 JOIN 路径选择不属于审查范围。

""".strip()


SELECT_RESULT_TABLE_REVIEW_PROMPT = """


## SELECT 结果表
  - 先判断 BIRD 答案值槽：question/evidence 最终要求返回的值、属性或指标。
  - SELECT 默认只返回答案值槽；筛选、排序、分组、连接和计算字段只用于定位候选集合。
  - `X of Y`、`X for Y`、`Y's X` 这类题面默认返回 `X`；`Y` 是定位答案的实体或范围，只有题面明确要求展示 `Y` 时才加入 SELECT。
  - `which/who/what/when/how many` 后面的核心疑问词决定答案值槽；介词短语引出的实体和范围默认是条件。
  - `indicate/show/list/give/find` 后面的短语只有在列举返回属性时加入 SELECT；如果只是说明答案值槽类型或筛选范围，不扩展 SELECT。
  - `Which/Who ...? Indicate X`、`Which/Who ...? Please indicate X` 这类题面中，前半句通常用于定位记录，`X` 是答案值槽；除非题面明确要求展示前半句实体，否则 SELECT 返回 `X`。
  - question 用逗号、and/or 并列列出多个返回属性时，SELECT 保留这些并列属性；不要只返回最后一个属性，也不要因为某个属性用于排序或筛选就从结果表删除。
  - 题面同时要求指标和标识信息时，SELECT 同时返回指标和标识信息；例如 `How many ...? Indicate full name` 返回数量/数值以及 full name。
  - 题面问页面、链接、URL 时，SELECT 链接字段本身；对应实体名、年份、编号、日期和轮次只作为定位条件。
  - 题面问 when/date/time 时，SELECT 返回对应日期或时间字段；年份、赛季、地点和实体名默认只用于定位，除非题面明确要求展示。
  - 题面问 list races/schools/drivers/circuits 等实体集合时，SELECT 该实体的主显示名；年份、日期、位置、ID、代码、计数等只在题面明确要求时加入 SELECT。
  - 题面问时间、耗时、成绩、位置、排名、数量、百分比等指标时，SELECT 指标本身；产生该指标的实体默认不展示。
  - evidence 使用 `A refers to B` 或 `A = B` 明确说明答案字段时，SELECT 优先返回 `B` 对应字段；解释短语中的实体只用于筛选或连接。
  - 人名全名按原始字段分列返回，优先 `forename, surname`。
  - evidence 明确给出返回字段、公式结果或列顺序时，按 evidence 决定 SELECT。
  - 审查时列出期望 SELECT 和当前 SELECT；对额外列、漏列、顺序不一致、答案值槽不一致给出一次性修改建议。

## 审查思路
你负责审查候选 SQL 的 SELECT 结果表：
- 只审查 SELECT 列、列顺序、答案对象和结果表形状。
- 候选 SQL 执行结果预览用于核查结果列数、列值类型、是否把定位字段混进答案表、是否漏掉题面并列要求的答案列。
- 反馈一次性给出所有 SELECT 修改建议。
- 如果需要修改，直接给出拒绝理由和修改建议；只有完全符合时才输出 OK。
- 条件、公式、排序、聚合、窗口函数、表、字段、枚举值和 JOIN 路径交给其他审查器。

""".strip()


_TIME_LITERAL_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b")
_TEXT_TIME_COL_RE = re.compile(r"\b(?:q1|q2|q3|time|duration|fastestLapTime)\b", re.IGNORECASE)
_AGG_RE = re.compile(r"\b(?:SUM|AVG|COUNT|MIN|MAX)\s*\(", re.IGNORECASE)
_COUNT_DISTINCT_RE = re.compile(r"\bCOUNT\s*\(\s*DISTINCT\b", re.IGNORECASE)
_GROUP_RE = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)
_NAME_COL_RE = re.compile(r"\b(?:forename|surname|name|driverRef|code)\b", re.IGNORECASE)
_METRIC_WORD_RE = re.compile(
    r"\b(?:time|duration|position|rank|speed|lap|point|points|wins|count|number|percent|percentage|rate|ratio)\b",
    re.IGNORECASE,
)
_EVIDENCE_SQL_EXPR_RE = re.compile(
    r"\b(?:AVG|SUM|COUNT|MIN|MAX)\s*\(\s*[^)]+\s*\)"
    r"|(?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*\s*(?:=|<|>|<=|>=|LIKE)\s*'[^']+'"
    r"|(?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*\s*(?:=|<|>|<=|>=)\s*\d+",
    re.IGNORECASE,
)


def build_dynamic_review_guidance(case: BirdCase, sql: str) -> str:
    """Return short BIRD-style guidance relevant to the current question/SQL."""

    prompt_text = f"{case.question}\n{case.evidence}"
    guidance: list[str] = []
    evidence_anchors = _extract_evidence_sql_anchors(case.evidence)
    if evidence_anchors:
        guidance.append(
            "evidence 给出的 SQL 口径锚点需要作为审查基准："
            + "、".join(f"`{anchor}`" for anchor in evidence_anchors)
            + "；候选 SQL 应保留这些列、函数、比较方向和数值尺度。"
        )

    if _METRIC_WORD_RE.search(prompt_text) and _NAME_COL_RE.search(sql):
        guidance.append(
            "指标题先锁定最终指标列；用于说明该指标归属的实体名、代码或年份只在题面明确要求展示时进入 SELECT。"
        )

    if re.search(r"\b(?:indicate|please indicate|show|give)\b", prompt_text, re.IGNORECASE) and _NAME_COL_RE.search(sql):
        guidance.append(
            "`Which/Who ...? Indicate X` 题型中，先判断前半句实体是否只是定位记录；如果是，SELECT 应返回 `X`，不自动加入实体名或代码。"
        )

    if re.search(r"\b(?:each|per|for each|by)\b", prompt_text, re.IGNORECASE):
        guidance.append(
            "`each/per/by/for each` 先决定一行代表什么；保持题面要求的行粒度，但不要因为行属于某个实体就自动把实体名、编号或序号加入 SELECT。"
        )

    if _AGG_RE.search(sql) or _GROUP_RE.search(sql):
        guidance.append(
            "聚合只用于题面要求的总数、平均、最大最小或分组结果；排序取首/前 N 通常在最终候选行上 ORDER BY/LIMIT，不先按实体聚合。"
        )

    if _COUNT_DISTINCT_RE.search(sql):
        guidance.append(
            "候选 SQL 使用了 COUNT(DISTINCT ...)；需要审查题面问的是去重主实体数，还是满足条件的明细记录数。"
        )

    if re.search(r"\b(?:percent|percentage|rate)\b", prompt_text, re.IGNORECASE):
        guidance.append(
            "百分比/完成率题返回百分数尺度；分子和分母使用同一题面范围内的行集合。"
        )

    if _TIME_LITERAL_RE.search(prompt_text) and _TEXT_TIME_COL_RE.search(sql):
        guidance.append(
            "题面时间字面量与数据库文本时间格式可能不同；对文本时间列优先按存储格式做前缀匹配，再决定是否需要解析计算。"
        )

    if re.search(r"\b(?:top|bottom|most|least|highest|lowest|fastest|slowest|earliest|latest|first|last)\b", prompt_text, re.IGNORECASE):
        guidance.append(
            "排序题先完成题面范围限定，再按题面排序指标取行；SELECT 仍只返回答案值槽。"
        )

    if re.search(r"\btime format\b|格式", prompt_text, re.IGNORECASE) and re.search(r"\b(?:MIN|ORDER\s+BY)\s*\(?\s*(?:\w+\.)?time\b", sql, re.IGNORECASE):
        guidance.append(
            "evidence 明确说明时间文本格式时，最短/最快时间需要按该格式换算后比较；不要用 `MIN(time)` 或 `ORDER BY time` 的字符串字典序代替时间大小。"
        )

    if not guidance:
        return ""
    lines = ["## 本题动态审查要点", *[f"- {item}" for item in guidance]]
    return "\n".join(lines)


def _extract_evidence_sql_anchors(evidence: str, *, limit: int = 5) -> list[str]:
    anchors: list[str] = []
    for match in _EVIDENCE_SQL_EXPR_RE.finditer(evidence or ""):
        anchor = " ".join(match.group(0).strip().split())
        anchor = _normalize_evidence_anchor(anchor)
        if anchor and anchor not in anchors:
            anchors.append(anchor)
        if len(anchors) >= limit:
            break
    return anchors


def _normalize_evidence_anchor(anchor: str) -> str:
    func_match = re.match(r"(?is)^(AVG|SUM|COUNT|MIN|MAX)\s*\(\s*(.*?)\s*\)$", anchor)
    if not func_match:
        return anchor
    func = func_match.group(1).upper()
    expr = func_match.group(2).strip()
    distinct = ""
    if re.match(r"(?is)^DISTINCT\b", expr):
        distinct = "DISTINCT "
        expr = re.sub(r"(?is)^DISTINCT\b", "", expr).strip()
    expr = re.split(r"(?i)\s+where\s+", expr, maxsplit=1)[0].strip()
    return f"{func}({distinct}{expr})" if expr else anchor


def build_business_initial_request(case: BirdCase) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    return f"""\
数据库项目：{case.db_id}
业务问题：{case.question}
补充提示：{evidence}
"""


def build_pontis_plan_request(case: BirdCase) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    return f"""\
数据库项目：{case.db_id}
业务问题：{case.question}
补充提示：{evidence}

请完成数据库分析，并按要求提交拟执行的 SQL。
"""


def build_select_result_table_review_request(case: BirdCase, sql: str, execution_preview: str | None = None) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    dynamic_guidance = build_dynamic_review_guidance(case, sql)
    dynamic_block = f"\n{dynamic_guidance}\n" if dynamic_guidance else ""
    execution_block = f"\n候选 SQL 执行结果预览：\n{execution_preview}\n" if execution_preview else ""
    return f"""\
数据库项目：{case.db_id}
业务问题：{case.question}
补充提示：{evidence}
{dynamic_block}

候选 SQL：
```sql
{sql}
```
{execution_block}

请只审查 SELECT 结果表。
"""


def build_sql_output_review_request(case: BirdCase, sql: str, execution_preview: str | None = None) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    dynamic_guidance = build_dynamic_review_guidance(case, sql)
    dynamic_block = f"\n{dynamic_guidance}\n" if dynamic_guidance else ""
    execution_block = f"\n候选 SQL 执行结果预览：\n{execution_preview}\n" if execution_preview else ""
    return f"""\
数据库项目：{case.db_id}
业务问题：{case.question}
补充提示：{evidence}
{dynamic_block}

候选 SQL：
```sql
{sql}
```
{execution_block}

请审查条件、公式、排序、聚合、窗口函数和 SQL 写法。
"""
