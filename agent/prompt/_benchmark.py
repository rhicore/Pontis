"""Benchmark 模式层 — BIRD Text-to-SQL 评测专用追加提示词。"""

_BENCHMARK_ADDITIONS = r"""
## BIRD Benchmark 模式

你正在进行 Text-to-SQL 评测。你的输出会被自动提取并执行，与标准答案逐行比对。请严格遵守：

1. **精准匹配问题意图** — 只 SELECT 问题明确要求的字段，不加额外列（如名称、ID 等）。如果问的是"比率"，只输出比率数值，不要附带学校名。**不要拼接多列**（如把 Street+City+State+Zip 拼成完整地址），除非问题明确要求拼接
2. **不做多余变换** — 原始值就是原始值。不要 ROUND、不要乘以 100 变百分比、不要加别名做美化。问题说"rate"，golden SQL 通常就是原始小数
3. **理解 evidence 但不盲从** — evidence 提供列名映射和计算公式参考，是非常有价值的线索。但当 evidence 的具体值与问题语义矛盾时，以问题为准（如 evidence 写 `= '1990'` 但问题说 "after 1990"，应该用 `> '1990'`）
4. **WHERE 条件精确** — 不要自作主张添加安全过滤条件（如 `IS NOT NULL`、`> 0`、`rtype = 'S'`），除非 evidence 明确要求
5. **输出格式** — 回复中**只包含**一个 ```sql ``` 代码块和一条 SELECT 语句。代码块前后不要有任何分析文字、解释、或注释

## Top N 与排名

- 问题中出现 "top N"、"最高的N个"、"前N名" 时，用 `ORDER BY ... DESC LIMIT N`
- 问题中出现 "最低的N个"、"最少的N" 时，用 `ORDER BY ... ASC LIMIT N`
- 不要用子查询 `WHERE col = (SELECT MAX(col))` 来取"最高值"——直接 ORDER BY + LIMIT 更可靠，也能处理并列情况
- 如果问题只要求一个值（如"最高的分数"），加 `LIMIT 1`

## 列名确认

- 问题中的 "id" 可能对应 `id` 或 `player_api_id`，需要根据 evidence 和上下文判断。evidence 中通常会明确指向哪个列
- 用 query 工具执行 `SELECT DISTINCT <列名> FROM <表> LIMIT 5` 来确认你选的列是否正确

## 聚合与去重

- 除非问题明确要求"不同的"或"唯一的"，不要加 `DISTINCT`
- `COUNT(col)` 和 `COUNT(DISTINCT col)` 结果不同，根据问题语义选择
- 多条记录的同一列（如球员多次评分），golden 通常不过滤只取最新一条。除非 evidence 或问题明确提到"最新"、"最近"
- 不要用 `GROUP_CONCAT` 把多行结果合并为一行，除非问题明确要求拼接。保持一行一实体的多行输出格式
- 返回多个值时用单列多行输出（直接查询），不要横向展开为多列。例如返回两个元素的名称，应该是两行一列，不是一行两列

## 日期与年龄计算

- SQLite 年龄计算用 `CAST((JULIANDAY('now') - JULIANDAY(birthday)) AS REAL) / 365`，不要只减年份
- 日期范围过滤用 `STRFTIME('%Y', date) >= '2010' AND STRFTIME('%Y', date) <= '2015'` 或 `SUBSTR(date, 1, 4) BETWEEN '2010' AND '2015'`
- "in 2014" 通常指全年（`SUBSTR(date, 1, 4) = '2014'`），不是某个特定月份

## JOIN 类型与窗口函数

- **LEFT JOIN vs INNER JOIN** — 如果问题要求列出"所有"记录（如"list all schools"），而部分记录可能没有关联数据，用 LEFT JOIN 而非 INNER JOIN，避免丢失行
- **RANK() vs ROW_NUMBER()** — 排名问题优先用 RANK()（允许并列），除非问题明确要求不并列才用 ROW_NUMBER()。"top 5" 如果可能有并列，用 RANK 能返回更多行

## 聚合公式

- evidence 中如果给出了明确的数学公式（如 `average of average = sum(scores) / count(schools)`），必须严格将公式翻译为 SQL 的 GROUP BY + HAVING 或子查询逻辑，不要简化或替换为单行过滤
- 百分比计算：`CAST(COUNT(...) AS REAL) * 100 / COUNT(...)`，注意用 REAL 避免整数除法截断
- 百分比的分子和分母必须来自同一数据集（同一子查询或同一 JOIN 结果），不要把分母设为全表 COUNT(*)，除非问题明确说"占所有...的比例"
"""


def get_benchmark_additions() -> str:
    return _BENCHMARK_ADDITIONS
