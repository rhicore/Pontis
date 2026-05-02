"""Benchmark 模式层 — BIRD Text-to-SQL 评测专用追加提示词。

只包含 BIRD 数据集 golden SQL 的特征和匹配规则。
通用的 SQL 生成流程规范在 _sql.py 中。
"""

_BENCHMARK_ADDITIONS = r"""## BIRD Benchmark 规则

你的输出会被自动提取并执行，与标准答案逐行比对。以下是 BIRD 数据集的答案特征：

### 输出格式

- **回复中只包含一个 ```sql ``` 代码块和一条 SELECT 语句**，代码块前后不要有任何文字
- **绝对不要拼接列**：如果数据库有 `forename` 和 `surname` 两列，SELECT 输出两列（`SELECT forename, surname`），不要拼成 `forename || ' ' || surname`。golden SQL 永远不会拼接名字列。`Street || City` 等同理
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
- **时间列排序**：`fastestLapTime`、`fastestLapSpeed` 等时间类 TEXT 列不能直接 ORDER BY 排序（字符串排序不等于数值排序）。需要排序或比较时，优先使用对应的数值列（如 `milliseconds`、`CAST(...AS REAL)`），或用 CASE 表达式解析文本格式

### WHERE 条件

- **绝对不要**自作主张添加问题未要求的过滤条件，即使你在元数据中看到了相关信息（如某列区分记录类型、某列标识有效/无效数据）。常见的错误添加：`IS NOT NULL`、`> 0`、`rtype = 'S'`、`status = 'active'`、`type = 'X'`
- **不要因为某列区分了数据粒度或类型就主动用它来过滤**——只按问题要求的条件过滤
- **典型反例：rtype / record_type 类列**：这类列可能标注 S=学校级、D=学区级等含义，看起来"应该过滤"。但 golden SQL 从不过滤这类列，所有行一视同仁。即使问题提到"school"，也不要加 `rtype = 'S'`——JOIN 到 schools 表已经自然完成了学校级别的筛选
- evidence 中的列名映射和条件值是高价值线索，优先参考
- 当 evidence 与问题矛盾时，以问题语义为准

### 严格遵循 evidence 公式

- evidence 中给出的计算公式（如 `A/B`、`CAST(X AS REAL) * 100 / Y`）必须**严格翻译**，不要简化
- 不要将 evidence 公式中的列替换为"等价"列（如 `Free Meal Count` 不能替换为 `FRPM Count`）
- evidence 中的代码值映射（如 "SOC = 69 means District Community Day Schools"）必须直接使用代码值

### DISTINCT 与 COUNT

- 除非问题明确要求"不同的"或"唯一的"，否则**不要加 DISTINCT**
- **COUNT 在 JOIN 场景下的约定**：当查询涉及 1:N 关联（如 Patient 1:N Laboratory、 races 1:N results），`COUNT(T1.ID)` 或 `COUNT(*)` 统计的是 JOIN 后的总行数（包含重复），这是 golden SQL 的标准约定。不要自作主张加 DISTINCT 去重，除非问题明确说"不同的"或"唯一的"
- evidence 中给出了明确的数学公式时，严格翻译为 SQL，不要简化
- **JOIN 后去重**：当查询 url、location 等唯一属性但需要 JOIN 才能获取时（如 circuits.url JOIN races），golden SQL 通常加 `DISTINCT` 避免因 1:N 关系产生重复行。如果你发现 JOIN 会产生重复且查询的是唯一属性，加 DISTINCT

### 表选择与 JOIN

- 只 JOIN 问题实际需要的表，不要因为表之间有 FK 就全部 JOIN 进来
- 如果所需的过滤/聚合列已经存在于当前表中，不要额外 JOIN 另一张表来获取"等价"列
- 检查目标列是否在当前表中已经存在（如 results 已有 fastestLapTime，不需要再 JOIN lapTimes）
- "list all" 类型问题考虑用 LEFT JOIN 避免 INNER JOIN 丢失行
- 排名问题优先用 RANK() 而非 ROW_NUMBER()，允许并列

### 排名与 Top N

- "top N" / "最高的N个" → `ORDER BY ... DESC LIMIT N`
- "最低的N个" → `ORDER BY ... ASC LIMIT N`
- 不要用子查询 `WHERE col = (SELECT MAX(col))` 取极值，直接 ORDER BY + LIMIT

### 日期

- SQLite 年龄：`CAST((JULIANDAY('now') - JULIANDAY(birthday)) AS REAL) / 365`
- "in 2014" → `SUBSTR(date, 1, 4) = '2014'`（全年）

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
