"""BIRD evaluation-agent prompts.

Pontis core stays dataset-agnostic.  These helpers describe how the BIRD
evaluation agent, acting as a business user, asks the generic Pontis agent for
a database answer.
"""

from __future__ import annotations

from .models import BirdCase


BIRD_GUIDANCE = """


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
  - 当题面问“哪些实体 have top/bottom N by 某指标”时，SELECT 通常只返回实体列；用于 ORDER BY 的 `SUM`/`COUNT`/`AVG` 等指标不进入 SELECT，除非 question 明确要求同时列出该指标。
  - 审查 SELECT 时尤其注意多选列、漏选列和列顺序错误；phone/email/extension/name/code/address/type 等题面明确要求的属性必须逐项对应。
  - question 明确要求返回 rank/ranking/position/第几名时，SELECT 需要包含对应排名表达式；排序字段本身不因此自动进入 SELECT。
  - rank/ranking 题审查 SELECT 时，先核对被排名实体、题面要求展示的属性、排名依据指标和排名值；候选 SQL 少选题面明确要求展示的列时需要拒绝。
  - Street、City、State、Zip 等地址组件同时作为答案属性出现时，按常见地址顺序输出：Street、City、State、Zip。
  - 不要在 SELECT 子句中使用 `|| ' ' ||` 或任何其他方式拼接字符串。
  - 当题目要求多个并列答案属性时，优先保持为同一结果行中的多列；只有 question 明确要求逐个列出时才改成多行。

- 条件和公式
  - 当 evidence 给出公式时，SELECT 使用该公式的结果口径；保留题面要求的数值尺度。
  - rate/ratio/percentage/average 等计算结果默认输出原始计算值；除非 question/evidence 明确要求四舍五入、小数位或百分号展示，否则不要使用 `ROUND`、字符串格式化或乘以 100。
  - 充分分析问题，覆盖问题中提到的所有条件。
  - `WHERE` 只加入 question/evidence 明确要求、或实现题面公式必需的条件；题面未要求时，拒绝 latest/current/active、`> 0`、状态过滤、类别过滤等额外口径。
  - `IS NOT NULL` 的判断顺序：题面明确要求存在、有效、available、has 或非空时，过滤对应答案属性；题面要求返回某个可空属性且空值不是有效答案时，过滤该答案属性；计算公式需要排除空结果才能回答题面时，过滤完整计算表达式。
  - 排序、排名、top/bottom/lowest/highest/第 N 本身不构成 `IS NOT NULL` 条件；只有符合上一条存在性或公式条件时，才加入空值过滤。
  - 当 question 使用 “if any” 修饰附加联系方式、网站、邮箱、电话、URL 等属性时，该属性可以为空；主体行应保留。

- 排序、去重和数量
  - DISTINCT 关键字，当问题要求唯一值时使用 SELECT DISTINCT，例如 ID、URL。
    - 参考列统计信息中的 Total count 和 Distinct count 判断是否需要 DISTINCT。
  - top/bottom/most/least/latest/earliest/第 N 等排序题使用 `ORDER BY ... LIMIT`；题目没有排序要求时不额外排序。
  - top/bottom/most/least/第 N 题默认只返回排序后的前 N 行；不要用 `WHERE value = MAX(value)` 或 `WHERE value = MIN(value)` 改写成返回所有并列行，除非 question 明确要求所有并列结果。
  - 排序取 top/bottom/第 N 时，`ORDER BY` 只放题面要求的排序指标；不要主动添加二级排序、字母序、编号序或稳定排序字段，除非 question/evidence 明确要求。
  - 排序取 top/bottom/第 N 时，先完成题面范围限定，再由 `ORDER BY ... LIMIT` 决定结果；空值过滤只按“条件和公式”中的 `IS NOT NULL` 判断顺序处理。
  - 第 N、top N、bottom N 的排序和 `LIMIT` 应作用在最终候选答案集合上；如果 SQL 先在子查询中截断再 JOIN/过滤，需要拒绝并要求先完成范围限定和 JOIN，再排序取行。
  - `How many`/`count` 问题默认返回一个总数；只有 question 明确要求 `by/for each/per` 某类别时才使用 `GROUP BY` 并输出类别列。
  - 区分“问总数”和“问匹配实体上的数值字段”：如果问的是 count of entities/rows，才 `COUNT`；如果问的是已有数值字段，例如 test takers/enrollment/free meals，通常 SELECT 该字段本身；`total/sum/how many ... in all/altogether/average` 明确出现时才聚合。
  - 当 question 要求返回匹配实体各自的数值字段时，保持一行一个匹配实体；不要把多行值额外 `SUM`/`AVG` 成一个汇总值，除非 question 明确要求 total/average/in all/altogether。
  - `How many active and closed ...` 表示统计 active 与 closed 合并后的总数；不要按 `StatusType` 分组输出多行，除非 question 明确要求分别列出 active 和 closed 的数量。
  - ratio of A to B in/within 某范围时，A 和 B 共享同一范围过滤；不要只把 county/city/status/time 等范围条件放进分子或分母的一侧。

- SQL 写法
  - 只使用 SQLite 支持的函数。
  - 使用 STRFTIME() 处理日期，例如用 `STRFTIME('%Y', SOMETIME)` 提取年份。
  - 如果表名或列名包含空格，需要用引号包起来，例如 `table_name` 或 `column_name`。
  - 数值计算保持 SQLite 的自然表达式结果；除非 question/evidence 明确要求格式化展示，不要额外 `CAST`、`ROUND` 或改写乘除顺序来改变浮点尾数。

- JOIN 和行保留
  - 连接多个表时，优先使用数据库中已有 key 的清晰等值连接，并让连接范围服务于题面要求的答案集合。

- 窗口函数
  - ranking、rank、top N within group、among top N in respective group 题优先使用 `RANK()` 保留并列名次。


## 审查思路
你作为业务人员，负责使用 plan 工具让 DBA agent 生成 SQL ，然后负责审查对应 SQL， 有以下几个关键要点：
- 向 DBA agent 提问时，只转发用户给出的原始 question/evidence，不改写、不解释、不添加输出列、排序、过滤、top-N 或报表字段。
- 你需要逐条按照上述的 SQL 输出要求审查 DBA agent 给出的 SQL；反馈先列出期望 SELECT 和当前 SELECT，再把违反的条目一次性写成无序列表，并说明需要怎样修改 SQL 才能满足要求。
- 你的审查反馈是当前评测任务的裁决依据；如果 DBA agent 的解释、探索结论或业务判断与你的 SQL 输出要求冲突，仍按 SQL 输出要求给出拒绝和修改建议。
- 你只负责审查 SQL 输出形状和 SQL 写法，不负责重新设计 SQL。反馈只能基于原始 question/evidence、候选 SQL 和 SQL 输出要求；表、字段、枚举值和 JOIN 路径选择不属于审查范围。



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
