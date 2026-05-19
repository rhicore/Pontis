# bird

这里是 BIRD 数据集跨库经验库的README

该库用途：
- 存放可迁移的 Text-to-SQL 经验(主要是example query)
- 帮助 agent 在新库上避免常见错误



## BIRD SQL 约定与风格

### 结果列

- 只选择问题明确要求的字段，不附带额外列
- 不要把多个字段拼成一个显示列；如姓名、地址等，保持原列输出
- 多个结果值优先按单列多行返回，不做横向展开或字符串聚合

### 过滤条件

- 不要自作主张添加问题未要求的过滤条件
- 即使元数据提示某列区分记录类型、有效性或粒度，也不要默认加过滤
- 当 evidence 给出了条件值或代码映射，直接按 evidence 翻译
- 当 evidence 与题目表面措辞冲突时，以题目真实语义为准，但不要偏离 evidence 明确给出的公式或编码

### Evidence 翻译

- evidence 给出的列名映射，优先使用
- evidence 给出的计算公式应严格翻译为 SQL，不要简化或改写
- evidence 给出的条件值，直接使用，不要猜测其他值
- evidence 中的代码值映射应直接使用，不要再猜测别的含义
- 当 evidence 明确指出应使用某列时，不要私自换成你认为更接近的列
- 如果 evidence 已经明确给出判断规则，就直接按 evidence 写 SQL，不要再为了“确认同一规则”做多轮试探

### DISTINCT 与 COUNT

- 没有“不同的”“唯一的”这类明确要求时，不要默认加 DISTINCT
- 在 1:N JOIN 中，`COUNT(*)` 或 `COUNT(T1.id)` 统计的是 JOIN 后的行数；不要擅自去重
- 只有当 JOIN 会引入重复、而题目要的是唯一属性结果时，才考虑 DISTINCT

### JOIN 选择

- 只 JOIN 问题真正需要的表
- 如果当前表已经有目标列，不要为了“更标准”再 JOIN 另一张等价表
- 在写 SQL 之前，先确认目标列是否已存在于当前表
- `list all` 一类问题，若担心 INNER JOIN 丢行，可考虑 LEFT JOIN
- 写 JOIN 前先确认 `fk` / `rel` / `overlap` / `disambig`
- `fk` 可靠性最高；`rel` 只作辅助；`overlap` 不能直接当 JOIN 条件

### 排序、极值与 Top-N

- `top N`、最高 N 个、最低 N 个，一般优先 `ORDER BY ... LIMIT N`
- 但如果题意允许并列极值，或要求返回所有并列最值，不要机械使用 `LIMIT 1`
- 排名问题若允许并列，优先选择能保留并列语义的写法
- 时间或数值以文本存储时，不要直接按字符串排序；先确认是否存在对应数值列，或显式转换
- 对 `the best / the highest / the richest ...` 这类单数最高级，如果 evidence 已经明确映射到 `max(column)` 且题目没有显式数量词，就直接 `ORDER BY column DESC LIMIT 1`
- `majority` / `most of` 这类“多数/大多数”表达，不等于最高级 `most`；默认先理解成分布或占比问题，优先 `GROUP BY`，不要机械加 `ORDER BY COUNT(*) DESC LIMIT 1`

### 文本数值

- 若某列以 TEXT 存储金额、百分比、时长等带格式的数值，且元数据 / README / 知识已明确这一点，先做清洗与类型转换后再比较或排序
- 不要把证据里的字符串字面量误当作字符串排序规则

### 复合查询

- SQLite 里如果每个复合查询分支都要各自 `ORDER BY / LIMIT`，不要写成顶层 `(SELECT ... LIMIT 1) UNION ALL (SELECT ... LIMIT 1)`
- 先放进 `WITH` / 子查询，再在外层组合

### 限制性定语从句

- 当题目写成 `the X which is cited / used / ordered ... most/least` 这类限制性定语从句时，候选集合应先限制为真正参与该关系的实体
- 不要为了求最小值而用 `LEFT JOIN` 把 `0` 次实体引进来，除非题目显式要求包含 `zero` / `none` / `never`

### 有序端点 / 成对关系

- 对成对关系表、桥接表或有序端点表（如 `*_id1 / *_id2`, `src / dst`, `from / to`）要特别克制
- 题面里出现 `pair`、`both`、`another`，不自动等于“必须双侧对称约束”或“必须同时取两端属性”
- 在 README、FK、已有知识没有明确要求双侧对称时，先从一个已锚定的端点出发建最小 JOIN，再判断是否真的需要补第二侧约束或自连接

