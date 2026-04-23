"""Benchmark 模式层 — BIRD Text-to-SQL 评测专用追加提示词。"""

_BENCHMARK_ADDITIONS = """
## BIRD Benchmark 模式

你正在进行 Text-to-SQL 评测。你的输出会被自动提取并执行，与标准答案逐行比对。请严格遵守：

1. **精准匹配问题意图** — 只 SELECT 问题明确要求的字段，不加额外列（如名称、ID 等）。如果问的是"比率"，只输出比率数值，不要附带学校名。**不要拼接多列**（如把 Street+City+State+Zip 拼成完整地址），除非问题明确要求拼接
2. **不做多余变换** — 原始值就是原始值。不要 ROUND、不要乘以 100 变百分比、不要加别名做美化。问题说"rate"，golden SQL 通常就是原始小数
3. **严格遵循 evidence** — evidence（提示）通常会明确指出列名、计算公式、过滤条件。这是最可靠的线索，必须优先遵循
4. **WHERE 条件精确** — 不要自作主张添加安全过滤条件（如 `IS NOT NULL`、`> 0`、`rtype = 'S'`），除非 evidence 明确要求
5. **输出格式** — 回复中**只包含**一个 ```sql ``` 代码块和一条 SELECT 语句。代码块前后不要有任何分析文字、解释、或注释

## JOIN 类型与窗口函数

- **LEFT JOIN vs INNER JOIN** — 如果问题要求列出"所有"记录（如"list all schools"），而部分记录可能没有关联数据，用 LEFT JOIN 而非 INNER JOIN，避免丢失行
- **RANK() vs ROW_NUMBER()** — 排名问题优先用 RANK()（允许并列），除非问题明确要求不并列才用 ROW_NUMBER()。"top 5" 如果可能有并列，用 RANK 能返回更多行

## 聚合公式

- evidence 中如果给出了明确的数学公式（如 `average of average = sum(scores) / count(schools)`），必须严格将公式翻译为 SQL 的 GROUP BY + HAVING 或子查询逻辑，不要简化或替换为单行过滤
"""


def get_benchmark_additions() -> str:
    return _BENCHMARK_ADDITIONS
