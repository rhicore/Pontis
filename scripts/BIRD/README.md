# bird

这是 BIRD 数据集的跨库经验库。

用途：
- 存放可迁移的 Text-to-SQL 经验
- 帮助 agent 在新库上避免常见错误
- 只记录跨库可复用规则，不记录单库事实

写入范围：
- 只允许写 `convention`、`pattern`、`lesson`、`example`
- 新建知识时使用 `bird::<short_name>:knowledge:<type>`
- 若现有知识只是需要补充、修正或增加证据，优先更新，不要重复创建

禁止写入：
- 术语词典、字段释义、行业名词解释
- 仅适用于单个数据库的局部事实
- 一次题目偶然做对或做错但没有可迁移性的经验

## 读取顺序

1. 如果当前同时打开了多个项目，先把这些项目里存在的 `README` 读完，再做任何其他操作。
2. 读取 README 时，推荐直接使用 `meta({"ref": "<project>::README", "property": ["detail"]})`。
3. 不要求固定顺序；多个项目里，先读哪个 README 都可以。
4. 如果某个项目明确返回 README 不存在，就直接继续，不要反复重试。

## 数据库探索纪律

1. 读完 README 后，先理解当前数据库的 schema、列语义、关系和消歧信息。
2. `query` 只用于验证，不要拿来代替 schema 探索。
3. 用定向 `glob` 找数据库、表、列、fk、disambig；不要把 `glob("*")` 当成默认起手式。
4. 若某个 `db/*:fk` 入口为空，再退回项目级 `*:fk`。
5. 若某列 `meta` 已明确没有 `sample/topk` 等字段，不要重复追问；直接改用一次最小 `query` 验证。
6. 如果 README、列元数据或知识节点已经明确给出可执行规则，不要为了重复确认同一规则继续连做多次 `query`。
7. 优先复用工具返回的完整展示 ref；`glob` / `search` 返回什么，就尽量原样拿去喂给 `meta` / `update_meta` / `add_edge`。
8. `meta({"ref": "<project>::README"})` 如果明确返回不存在，就直接继续，不要反复重试 README。
9. README、CSV、JSON、文本文件如果 `meta(detail)` 已经给出可读内容，就不要再 `bash cat` 原文件。
10. 找数据库文件时，优先用 `*:file:db`，不要只猜 `*.db`。

## bird 知识的读取方式

1. 在输出最终 SQL 之前，至少浏览一次 `bird` 里的知识实体总表，看看有没有相关经验。
2. 推荐先用 `glob("bird::*:knowledge")`，但把它当索引页，不要靠翻很多页硬扫。
3. 如果总表候选很多，立即用 `search(ref="bird::*:knowledge", query="...")` 缩到 1-3 个最相关实体，再用 `meta` 深读。
4. 搜索词优先用题目里的核心名词、evidence 里的公式词、以及你怀疑的错误模式词。

### 抽象知识优先

优先读取以下抽象知识实体：
- `knowledge:convention`：规则 / 约定
- `knowledge:pattern`：通用解法模式
- `knowledge:lesson`：反面教训
- `knowledge:term`：术语或概念说明

`knowledge:example` 放在后面；只有当上述抽象知识仍不足以支持判断时，才把 example 当作解释型案例阅读。

如果先看到某个 example，也要回头优先查看它相连的抽象知识，再决定是否参考这个案例。

## Reflection 写入规则

1. 先查 `bird` 里最相关的已有知识，优先看抽象知识实体；不要一上来就新建。
2. 默认策略是：优先 `update`，谨慎 `create`。
3. 如果已有相似知识：
   - 内容相同：跳过，不重复创建
   - 内容互补：用 `update_meta` 补充 detail，并增加支持证据
   - 内容矛盾：只有在你能明确指出旧知识为何不成立时，才覆盖修正
4. 只有在确认没有合适的已有实体时，才 `create_entity`。
5. 如果某个已有实体只差补一句边界、补一个反例或补一个支持证据，就应该修改它，而不是再造一个新实体。
6. 如果已有知识的 `brief/detail` 是空、`-`、`...` 之类占位符，优先把它们改写成真实可读内容，而不是新增平行实体。
7. 如果最后没有足够强、足够硬的跨库经验，允许本轮什么都不写。

### Example 的要求

1. `knowledge:example` 不能孤立存在。
2. 只要保留或新建 example，就必须把它与对应的抽象知识实体连起来。
3. 这里的“对应抽象知识实体”指：`knowledge:convention`、`knowledge:pattern`、`knowledge:lesson`、`knowledge:term`。
4. 如果对应抽象知识还没有，就先补抽象知识，再连边。
5. `example` 的 `brief` 先写可迁移结论，再写题号 / 库名等案例信息；不要把 brief 写成原题复述。
6. `example` 的 `detail` 先给 `transfer_hint`、`mistake_summary`、`why_this_case_matters` 这类抽象内容，再附 question / evidence / golden_sql 等案例证据。

## BIRD SQL 约定

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

- evidence 中给出的计算公式应严格翻译，不做“等价替换”
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
- `majority` / `most of` 这类“多数/大多数”表达，不等于最高级 `most`；默认先理解成分布或占比问题，优先 `GROUP BY` 展示分布，不要机械加 `ORDER BY COUNT(*) DESC LIMIT 1`

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

## 使用方式

1. 先读本 README，再决定是否查看具体知识节点。
2. 若当前问题已被本 README 覆盖，优先遵循这里的高层约定。
3. 若需要更细的经验，再查看 `bird::*:knowledge`。
