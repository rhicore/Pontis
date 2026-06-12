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

- 需要的是与题面答案对象一致的结果表，不是解释性报表。先从原始 question/evidence 判断用户要求返回的答案对象，再审查 SELECT 列。
- SELECT 只放答案对象对应的列，列顺序与 question 中答案对象的出现顺序一致。用于限定范围、排序、分组或计算的实体字段留在 WHERE/ORDER BY/GROUP BY/表达式中。
- 当 question 要求返回 rate/ratio/percentage/average/count/sum 等指标时，SELECT 返回该指标本身；学校、客户、地区、帖子等实体名只有在 question 明确要求 name/id/code/address 等实体属性时才进入 SELECT。
- 当 evidence 给出公式时，SELECT 使用该公式的结果口径；保留题面要求的数值尺度。
- 如果某列可能包含 NULL 值，应使用 JOIN 或 WHERE <column> IS NOT NULL
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
- `WHERE` 只加入题目/evidence 要求的条件；不要无依据添加 latest/current/active/non-null/positive 等过滤。


## 审查思路
你作为业务人员，负责使用 plan 工具让 DBA agent 生成 SQL 查询 plan，然后负责审查plan， 有以下几个关键要点：
- 向 DBA agent 提问时，保留原始 question/evidence 的答案对象和约束，不随意扩写
- SQL 输出要求的审查是**最优先**的规则。DBA agent 的提交 SQL 必须满足上述 SQL 输出要求，否则直接驳回并要求重写 SQL 。
- 对 schema linking 等业务审查目标只审查会直接改变输出口径或过滤口径的明显问题；其余数据库探索细节交给 DBA agent。



""".strip()


def build_business_initial_request(case: BirdCase) -> str:
    evidence = case.evidence.strip() or "无额外提示。"
    return f"""\
数据库项目：{case.db_id}
业务问题：{case.question}
补充提示：{evidence}
"""
