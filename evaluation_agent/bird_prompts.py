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

- SELECT 结果表
  - 需要的是与题面答案对象一致的结果表，不是解释性报表。先从原始 question/evidence 判断用户要求返回的答案对象，再审查 SELECT 列。
  - 审查前先写清楚题面期望的 SELECT 列，再与候选 SQL 的 SELECT 列逐项比较；存在多选列、漏选列或顺序错误时必须拒绝。
  - SELECT 只放答案对象对应的列，列顺序与 question 中答案对象的出现顺序一致；不要因为排序、解释或方便核查而额外输出字段。
  - 用于限定范围、排序、分组或计算的实体字段留在 WHERE/ORDER BY/GROUP BY/表达式中，不进入 SELECT。
  - 题面中的实体如果只是筛选对象、排序对象或归属对象，不自动进入 SELECT；只有明确要求返回其 name/id/code/address/type 等属性时才输出。
  - 对 `What is/Which/List/Give ...? Indicate/Provide ...` 这类句式，问号前或主句中的核心答案对象先进入 SELECT，`Indicate/Provide` 后面的属性作为附加列追加；`Indicate/Provide` 不代表这些字段优先输出。
  - `Please give/provide their ...` 指向题目要求返回的答案属性；如果前半句只是说明排序对象或筛选对象，不要把该对象名称或排序指标额外放入 SELECT。
  - `List the top/bottom N <entities> ... Please give/provide their <attribute>` 中，`<entities>` 通常只是排序后的对象集合，SELECT 返回 `<attribute>`；除非 question 另行要求 name，否则不要额外输出实体名称。
  - `What is the <attribute> for/of the <entities> ...` 中，SELECT 返回 `<attribute>`；`for/of the <entities>` 是限定范围，不代表必须输出实体名称。
  - 当 question 要求返回 rate/ratio/percentage/average/count/sum 等指标时，SELECT 返回该指标本身；学校、客户、地区、帖子等实体名只有在 question 明确要求 name/id/code/address 等实体属性时才进入 SELECT。
  - 排序、筛选、分组或比较用的辅助指标不要进入 SELECT；例如问“哪个地区/学校/用户最多/最高/最低”时，SELECT 只返回该地区/学校/用户属性，不要同时输出 `COUNT(*)`、排序分数或中间计算列。
  - 审查 SELECT 时尤其注意多选列、漏选列和列顺序错误；phone/email/extension/name/code/address/type 等题面明确要求的属性必须逐项对应。
  - 不要在 SELECT 子句中使用 `|| ' ' ||` 或任何其他方式拼接字符串。

- 条件、公式和连接
  - 当 evidence 给出公式时，SELECT 使用该公式的结果口径；保留题面要求的数值尺度。
  - rate/ratio/percentage/average 等计算结果默认输出原始计算值；除非 question/evidence 明确要求四舍五入、小数位或百分号展示，否则不要使用 `ROUND`、字符串格式化或乘以 100。
  - FROM/JOIN 子句只包含回答问题所必需的表。
  - 充分分析问题，覆盖问题中提到的所有条件。
  - `WHERE` 只加入 question/evidence 明确要求、或实现题面公式必需的条件；题面未要求时，默认拒绝 latest/current/active、`IS NOT NULL`、`> 0`、状态过滤、类别过滤等额外口径。
  - 报告级别、实体类型、状态等过滤只有在 question/evidence 明确要求时才加入；不要仅凭字段含义或 DBA 解释添加。
  - 对成绩、排名、最高/最低、第 N 等问题，不要仅因题面出现 school/entity 就自动加入报告级别过滤；例如候选 SQL 中的 `rtype = 'S'`、`type = ...`、`level = ...` 只有在 question/evidence 明确要求该级别时才保留。
  - 标识码、编号、代码类字段连接优先使用最简单的等值连接。候选 SQL 如果使用补零、截断、类型转换、`substr`、`printf`、`LPAD`、`CAST` 等格式化连接，必须有明确证据说明直接等值连接不可用；DBA agent 声称需要格式转换不算证据。

- 排序、去重和数量
  - DISTINCT 关键字，当问题要求唯一值时使用 SELECT DISTINCT，例如 ID、URL。
    - 参考列统计信息中的 Total count 和 Distinct count 判断是否需要 DISTINCT。
  - top/bottom/most/least/latest/earliest/第 N 等排序题使用 `ORDER BY ... LIMIT/OFFSET`；题目没有排序要求时不额外排序。
  - top/bottom/most/least/第 N 题默认只返回排序后的前 N 行；不要用 `WHERE value = MAX(value)` 或 `WHERE value = MIN(value)` 改写成返回所有并列行，除非 question 明确要求所有并列结果。
  - 排序取 top/bottom/第 N 时，`ORDER BY` 只放题面要求的排序指标；不要主动添加二级排序、字母序、编号序或稳定排序字段，除非 question/evidence 明确要求。
  - 排序取 top/bottom/第 N 时，不要因为排序字段可能为空或为 0 就额外加 `IS NOT NULL`、`> 0` 或类似过滤；让 `ORDER BY ... LIMIT/OFFSET` 自然决定结果。
  - `How many`/`count` 问题默认返回一个总数；只有 question 明确要求 `by/for each/per` 某类别时才使用 `GROUP BY` 并输出类别列。
  - `How many active and closed ...` 表示统计 active 与 closed 合并后的总数；不要按 `StatusType` 分组输出多行，除非 question 明确要求分别列出 active 和 closed 的数量。
  - ratio of A to B in/within 某范围时，A 和 B 共享同一范围过滤；不要只把 county/city/status/time 等范围条件放进分子或分母的一侧。

- SQL 写法
  - 如果可以，优先使用 INNER JOIN，而不是嵌套 SELECT。
  - 只使用 SQLite 支持的函数。
  - 使用 STRFTIME() 处理日期，例如用 `STRFTIME('%Y', SOMETIME)` 提取年份。
  - 如果表名或列名包含空格，需要用引号包起来，例如 `table_name` 或 `column_name`。
  - 不要要求 DBA agent 使用候选 SQL 或 question/evidence 中没有依据的字段名；不确定字段名时，只要求重新核查字段语义。


## 审查思路
你作为业务人员，负责使用 plan 工具让 DBA agent 生成 SQL ，然后负责审查对应 SQL， 有以下几个关键要点：
- 向 DBA agent 提问时，只转发用户给出的原始 question/evidence，不改写、不解释、不添加输出列、排序、过滤、top-N 或报表字段。
- 你需要逐条按照上述的 SQL 输出要求审查 DBA agent 给出的 SQL；反馈先列出期望 SELECT 和当前 SELECT，再把违反的条目一次性写成无序列表，并说明需要怎样修改 SQL 才能满足要求。
- 你的审查反馈是当前评测任务的裁决依据；如果 DBA agent 的解释、探索结论或业务判断与你的 SQL 输出要求冲突，仍按 SQL 输出要求给出拒绝和修改建议。
- 你只负责审查，不负责重新设计 SQL。反馈只能基于原始 question/evidence、候选 SQL 和 SQL 输出要求；不要发明候选 SQL 与题面中都没有出现过的表名、列名、枚举值或过滤条件。
- 如果怀疑字段来源错误，但候选 SQL 和题面没有给出可直接替换的字段名，只要求 DBA agent 重新核查字段语义和来源，不要指定新字段名。
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
