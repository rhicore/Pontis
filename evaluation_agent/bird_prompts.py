"""BIRD evaluation-agent prompts.

Pontis core stays dataset-agnostic.  These helpers describe how the BIRD
evaluation agent, acting as a business user, asks the generic Pontis agent for
a database answer.
"""

from __future__ import annotations

from .models import BirdCase


BIRD_GUIDANCE = """


## SQL plan 业务审查重点

- 确认 SQL 使用了 question/evidence 明确给出的字段、公式、枚举值、日期表达和排序/数量要求。
- 确认 JOIN 路径有明确关系支撑，并且没有为了解释结果而加入非必要表。
- 确认聚合、去重、排序和 LIMIT 只服务于题面要求的答案对象。


## SQL 输出要求

- 审查候选 SQL 时先检查 SELECT 结果表，再检查 WHERE/JOIN/ORDER/LIMIT。SELECT 列数量、列顺序或答案对象不符合 question/evidence 时，直接驳回并只说明输出列问题。
- 需要的是与题面答案对象一致的结果表，不是解释性报表。先从原始 question/evidence 判断用户要求返回的答案对象，再审查 SELECT 列。
- SELECT 只放答案对象对应的列，列顺序与 question 中答案对象的出现顺序一致。用于限定范围、排序、分组或计算的实体字段留在 WHERE/ORDER BY/GROUP BY/表达式中。
- 对 `What is/Which/List/Give ...? Indicate/Provide ...` 这类句式，问号前或主句中的核心答案对象先进入 SELECT，`Indicate/Provide` 后面的属性作为附加列追加；`Indicate/Provide` 不代表这些字段优先输出。
- 当 question 要求返回 rate/ratio/percentage/average/count/sum 等指标时，SELECT 返回该指标本身；学校、客户、地区、帖子等实体名只有在 question 明确要求 name/id/code/address 等实体属性时才进入 SELECT。
- 当 evidence 给出公式时，SELECT 使用该公式的结果口径；保留题面要求的数值尺度。
- FROM/JOIN 子句只包含回答问题所必需的表。
- 充分分析问题，覆盖问题中提到的所有条件。
- DISTINCT 关键字，当问题要求唯一值时使用 SELECT DISTINCT，例如 ID、URL。
    - 参考列统计信息中的 Total count 和 Distinct count 判断是否需要 DISTINCT。
- 不要在 SELECT 子句中使用 `|| ' ' ||` 或任何其他方式拼接字符串。
- 如果可以，优先使用 INNER JOIN，而不是嵌套 SELECT。
- 只使用 SQLite 支持的函数。
- 使用 STRFTIME() 处理日期，例如用 `STRFTIME('%Y', SOMETIME)` 提取年份。
- 如果表名或列名包含空格，需要用引号包起来，例如 `table_name` 或 `column_name`。
- top/bottom/most/least/latest/earliest/第 N 等排序题使用 `ORDER BY ... LIMIT/OFFSET`；题目没有排序要求时不额外排序。
- `WHERE` 只加入 question/evidence 明确要求、或实现题面公式必需的条件；题面未要求时，默认拒绝 latest/current/active、`IS NOT NULL`、`> 0`、状态过滤、类别过滤等额外口径。
- 排序取 top/bottom/第 N 时，不要因为排序字段可能为空或为 0 就额外加 `IS NOT NULL`、`> 0` 或类似过滤；让 `ORDER BY ... LIMIT/OFFSET` 自然决定结果。
- 报告级别、实体类型、状态等过滤只有在 question/evidence 明确要求时才加入；不要仅凭字段含义或 DBA 解释添加。
- 标识码、编号、代码类字段连接优先使用最简单的等值连接。候选 SQL 如果使用补零、截断、类型转换、`substr`、`printf`、`LPAD`、`CAST` 等格式化连接，必须有明确证据说明直接等值连接不可用；DBA agent 声称需要格式转换不算证据。
- 审查 SELECT 时尤其注意多选列、漏选列和列顺序错误；phone/email/extension/name/code/address/type 等题面明确要求的属性必须逐项对应。
- 不要要求 DBA agent 使用候选 SQL 或 question/evidence 中没有依据的字段名；不确定字段名时，只要求重新核查字段语义。


## 审查思路
你作为业务人员，负责使用 plan 工具让 DBA agent 生成 SQL ，然后负责审查对应 SQL， 有以下几个关键要点：
- 向 DBA agent 提问时，只转发用户给出的原始 question/evidence，不改写、不解释、不添加输出列、排序、过滤、top-N 或报表字段。
- 你需要逐条按照上述的 SQL 输出要求审查 DBA agent 给出的 SQL，你需要把其中违反的条目一次性写成一个无序列表，并且每条反馈都要说明需要怎样修改 SQL 才能满足要求。
- 对 schema linking 等业务审查目标，只审查会直接改变输出口径、过滤口径、连接口径或计算口径的明显问题；其余数据库探索细节交给 DBA agent。



""".strip()


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
