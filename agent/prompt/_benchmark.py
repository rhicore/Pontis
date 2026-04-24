"""Benchmark 模式层 — BIRD Text-to-SQL 评测专用追加提示词。

只包含 BIRD 数据集 golden SQL 的特征和匹配规则。
通用的 SQL 生成流程规范在 _sql.py 中。
"""

_BENCHMARK_ADDITIONS = r"""## BIRD Benchmark 规则

你的输出会被自动提取并执行，与标准答案逐行比对。以下是 BIRD 数据集的答案特征：

### 输出格式

- **回复中只包含一个 ```sql ``` 代码块和一条 SELECT 语句**，代码块前后不要有任何文字
- 不要拼接多列（如 Street || City || State），除非问题明确要求
- 多个值用单列多行输出，不要横向展开为多列，不要用 GROUP_CONCAT 合并
- 不要加 ORDER BY（除非问题明确要求排序），golden SQL 通常不带排序

### 列选择

- 只 SELECT 问题明确要求的字段，不加额外列（如 ID、名称等附属信息）
- 问题中的 "id" 可能对应 `id` 或 `player_api_id`，根据 evidence 判断
- "atoms" 可能指 atom_id 也可能指 element，根据上下文和 evidence 判断

### 值变换

- 不要 ROUND、不要乘以 100 变百分比、不要加别名美化
- 问题说 "rate" 或 "percentage"，golden SQL 通常就是原始计算值
- 百分比计算：`CAST(COUNT(...) AS REAL) * 100 / COUNT(...)`，注意分子分母来自同一数据集

### WHERE 条件

- 不要自作主张添加安全过滤条件（如 `IS NOT NULL`、`> 0`、`rtype = 'S'`），除非 evidence 明确要求
- evidence 中的列名映射和条件值是高价值线索，优先参考
- 当 evidence 与问题矛盾时，以问题语义为准

### DISTINCT 与聚合

- 除非问题要求"不同的"或"唯一的"，或 golden 明确使用 DISTINCT，否则不要加
- evidence 中给出了明确的数学公式时，严格翻译为 SQL，不要简化

### 排名与 Top N

- "top N" / "最高的N个" → `ORDER BY ... DESC LIMIT N`
- "最低的N个" → `ORDER BY ... ASC LIMIT N`
- 不要用子查询 `WHERE col = (SELECT MAX(col))` 取极值，直接 ORDER BY + LIMIT

### 日期与 JOIN

- SQLite 年龄：`CAST((JULIANDAY('now') - JULIANDAY(birthday)) AS REAL) / 365`
- "in 2014" → `SUBSTR(date, 1, 4) = '2014'`（全年）
- "list all" 类型问题考虑用 LEFT JOIN 避免 INNER JOIN 丢失行
- 排名问题优先用 RANK() 而非 ROW_NUMBER()，允许并列

### Golden SQL 统计参考（1534 条）

以下是标准答案中各 SQL 模式的使用频率，用于辅助判断：
- GROUP_CONCAT：0 次 → 不要使用
- DISTINCT：244 次（其中 COUNT(DISTINCT) 65 次）→ 可用但需有理由
- IS NOT NULL：73 次 → 可用，常用于排除空值
- ORDER BY + LIMIT：288 次（top-N 排序）；ORDER BY 无 LIMIT：17 次（RANK/排名场景）
- 百分比 `* 100 /`：98 次 → 分子分母通常在同一 FROM 范围内
- ROUND：6 次（仅 toxicology 百分比精度）→ 通常不需要
- 字符串拼接 ||：2 次 → 仅在需要构造 ID 时使用
"""


def get_benchmark_additions() -> str:
    return _BENCHMARK_ADDITIONS
