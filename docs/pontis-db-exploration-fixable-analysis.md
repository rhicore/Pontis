# Pontis 对 DB_EXPLORATION_FIXABLE 错误的覆盖情况调研

调研对象：

- Bash Agent reflection run: `workspace/baselines/bash_agent/runtime_logs/bird_dev_20260524_031500_bash_agent_bird_dev_full_reflection_w50_fixed`
- Pontis zero-shot/no-readme run: `workspace/baselines/pontis/runtime_logs/bird_dev_bird_dev_full_noglobal_noreadme_reflection_20260523`

本文只关注 Bash Agent reflection 判定为 `DB_EXPLORATION_FIXABLE` 的错题。这个集合的含义是：在无知之幕下，只看当前 question/evidence/schema/full DB contents，就应该能通过更充分的数据库探索写出正确 SQL，不需要看其他 query-SQL pair，也不需要数据集先验。

## 总体结论

Bash Agent 当前有效识别出 `144` 个 `DB_EXPLORATION_FIXABLE` case，约占 BIRD dev 1534 题的 `9.4%`，和之前预估的 9%/150 题量级基本一致。

把这 144 个 case 和 Pontis `bird_dev_bird_dev_full_noglobal_noreadme_reflection_20260523` 对齐后：

| 指标 | 数量 |
|---|---:|
| Bash DB 探索可修正错例 | 144 |
| Pontis 同题做对 | 47 |
| Pontis 同题仍错 | 97 |
| Pontis 修复率 | 32.6% |
| Pontis 仍未修复比例 | 67.4% |

这说明 Pontis 的图谱确实解决了一部分纯 bash agent 的 DB 探索错误，但远没有把这类错误吃干净。按全量 dev 1534 题估算，剩余 `97` 个 Pontis 仍错的 DB-exploration-fixable case 对应约 `6.3` 个百分点的潜在空间。不过这 97 个里有一部分已经做对了数据库语义，最后输在 golden SQL 风格或输出形状上，因此“纯图谱改进”的真实上限会低于 6.3pp，但仍然是很大的可优化区域。

## 分库统计

| DB | Bash DB探索错例 | Pontis做对 | Pontis仍错 | Pontis修复率 |
|---|---:|---:|---:|---:|
| `formula_1` | 21 | 4 | 17 | 19.0% |
| `card_games` | 23 | 8 | 15 | 34.8% |
| `thrombosis_prediction` | 19 | 6 | 13 | 31.6% |
| `toxicology` | 19 | 7 | 12 | 36.8% |
| `student_club` | 11 | 2 | 9 | 18.2% |
| `european_football_2` | 10 | 2 | 8 | 20.0% |
| `codebase_community` | 13 | 6 | 7 | 46.2% |
| `california_schools` | 11 | 6 | 5 | 54.5% |
| `debit_card_specializing` | 8 | 4 | 4 | 50.0% |
| `superhero` | 5 | 1 | 4 | 20.0% |
| `financial` | 4 | 1 | 3 | 25.0% |

最值得优先看的是 `formula_1`、`card_games`、`thrombosis_prediction`、`toxicology`。这些库的 DB-exploration-fixable 错例多，且 Pontis 仍错数量高。

## Pontis 已经能修正的类型

这些 case 说明 Pontis 的图谱不是没有用，而是当前覆盖不均匀。

### 1. 精确候选列定位：`california_schools/q4`

问题问 direct charter-funded schools。Bash 选了 `schools.FundingType`，Pontis 做对：

```sql
frpm."Charter School (Y/N)" = 1
AND frpm."Charter Funding Type" = 'Directly funded'
```

Pontis 日志里先查了 `schools` 和 `frpm` 的列，再查了 `frpm."Charter Funding Type"` 的 top values，看到 `Directly funded`。这就是图谱 column brief + value sketch 起作用的典型 case。

改进启示：对自然语言短语匹配到多个候选列时，图谱的 enum/topk 信息很关键。`FundingType` 和 `Charter Funding Type` 这种近名字段必须同时展示，并让 agent 比较“哪个列更专门”。

### 2. 外键补全反范式字段缺失：`codebase_community/q576`

问题问评论 `"thank you user93!"` 的用户。Bash 直接读 `comments.UserDisplayName`，但该行为空。Pontis 做对：

```sql
comments.UserId = users.Id
SELECT users.DisplayName
```

Pontis 先查命中评论行，看到 `UserId=58`，再查 `users.Id=58`，得到 `DisplayName='Preets'`。

改进启示：图谱应记录“反范式字段缺失率”和“可通过外键补全的字段”。当命中行里的 display/name 字段为空时，agent 应自动检查实体表。

### 3. 直接实体属性优先于交易样本表：`debit_card_specializing/q1470`

问题问 CZE 的 Premium gas station 数量。Bash 绕到 `transactions_1k`，Pontis 做对：

```sql
FROM gasstations
WHERE Country = 'CZE' AND Segment = 'Premium'
```

Pontis 查了 `gasstations.Country`、`gasstations.Segment` 的 metadata，并且 disambig 里有 `segment` 同名概念。

改进启示：对实体计数问题，优先找实体主表上的直接属性，不应优先通过 sample/log/transaction 表反推。

### 4. 逐行条件 vs 聚合条件：`student_club/q1447`

问题证据明确 `underspend its budget refers to remaining > 0`。Bash 聚合后 `HAVING SUM(remaining) > 0`，Pontis 做对：

```sql
SELECT DISTINCT e.event_name, e.location
FROM event e
JOIN budget b ON e.event_id = b.link_to_event
WHERE b.remaining > 0
```

Pontis 查了 `budget` 样例，直接验证 `remaining > 0` 的逐行含义。

改进启示：当 evidence 写成简单谓词 `remaining > 0`，不要擅自提升到 entity-level SUM/HAVING。图谱可以记录一对多表的 row grain，并提示“条件应用在预算行粒度”。

## Pontis 仍未修正的类型

下面这些 case 更能说明 Pontis 现有 explorer/图谱策略还不够。

### 1. join key 规则和 join coverage 没有被系统化利用：`california_schools/q10`

问题问 SAT Reading 最高学校的 FRPM count。正确 SQL 直接：

```sql
satscores.cds = frpm.CDSCode
```

Pontis 仍错，预测用了 padding：

```sql
frpm.CDSCode = SUBSTR('00000000000000' || satscores.cds, -14)
```

Pontis 其实探索了 top SAT rows，也查了 `Mission San Jose High` 的 `cds` 长度和 `frpm.CDSCode`，但最后把 `schools` 表的 padding 规则错误迁移到了 `frpm` join。reflection 指出：直接 join 下最高分学校不匹配 frpm，应自然排除，下一条可匹配学校才是答案。

这说明 Pontis 现在的问题不是“没有工具”，而是没有把 join 规则做成强约束。它看到了很多局部信息，但没有执行候选 join path 的结果对比：

```sql
-- path A: direct join
SELECT s.cds, s.sname, s.AvgScrRead, f.CDSCode, f."FRPM Count (Ages 5-17)"
FROM satscores s JOIN frpm f ON s.cds = f.CDSCode
ORDER BY s.AvgScrRead DESC LIMIT 5;

-- path B: padded join
SELECT ...
FROM satscores s JOIN frpm f ON f.CDSCode = SUBSTR('00000000000000' || s.cds, -14)
ORDER BY s.AvgScrRead DESC LIMIT 5;
```

改进方向：新增 join candidate profiler。对每个候选 join path 记录 coverage、top-N 语义结果、是否 lossy、是否会把原本不匹配的行强行匹配上。

### 2. 已经查到关键证据，但最终选择错误 grain：`thrombosis_prediction/q1190`

问题问 proteinuria 正常范围患者中 UA 低于正常范围的百分比。evidence 给的是：

```text
calculation = MULTIPLY(DIVIDE(UA <= 6.5, U-PRO > 0 AND U-PRO < 30), 100)
```

golden 是行粒度：

```sql
SUM(CASE WHEN UA <= 6.5 THEN 1 ELSE 0 END) * 100 / COUNT(ID)
FROM Laboratory
WHERE `U-PRO` > 0 AND `U-PRO` < 30
```

Pontis 仍错，最终用了 `COUNT(DISTINCT ID)` 的 patient grain。关键是 Pontis 日志里已经同时验证了两种结果：

- record grain: `918` denominator, `560` numerator, `61.0%`
- patient grain: `68` denominator, `62` numerator, `91.18%`

但最后仍选择了 patient grain。这里不是探索不足，而是“探索结果没有被决策策略正确消费”。

改进方向：图谱/agent 应显式标注 evidence 中公式的计数对象。如果公式没有写 `DISTINCT`/unique/patient-level，而表本身是 `Laboratory` 多行记录，则默认保持 row grain；只有 question/evidence 明确 unique patient 才提升到 entity grain。

### 3. TEXT 数值列、空字符串和 SQLite 比较语义：`thrombosis_prediction/q1277`

问题问 `DNA < 8` 且 `Description IS NULL` 的 unique patients。golden 没有过滤空字符串：

```sql
T2.DNA < 8
```

Pontis 仍错。它探索了 `DNA` 列 metadata、`PRAGMA table_info`、`DISTINCT DNA`，但最终用了：

```sql
CAST(l.DNA AS REAL) < 8
```

或其他清洗式表达，导致和 SQLite 原生比较结果不一致。Bash reflection 发现：`DNA` 是 TEXT，库里有空字符串，SQLite 比较下这些值会影响结果。

改进方向：对所有“看似数值但类型为 TEXT”的列，图谱应记录：

- 空串数量
- NULL 数量
- 非数值 token 数量
- `col < threshold` 与 `CAST(col AS REAL) < threshold` 的结果差异

这类 profile 不应该等 query 时临时猜。

### 4. 结果形状/粒度自检不足：`formula_1/q881`

问题问某场 race 的 completion rate，evidence 是单个整体公式：

```text
DIVIDE(COUNT(driverid when time has value), COUNT(driverid)) as percentage
```

正确答案是一个 scalar。Bash 做成 per-driver career completion rate。Pontis 同题仍错，说明它也没有稳定地把“rate for the race”约束成单行聚合。

改进方向：在 final SQL 前做 result-shape sanity check：

- 问题问 `what is the rate/percentage` 且没有 `for each`，预期 1 行 1 列。
- 如果 SQL 有 `GROUP BY driverId` 或返回多行，应触发自我修正。

这不是 golden 风格，而是问题语义和结果形状的数据库理解。

### 5. 对复杂值格式做对了 DB 理解，但输在表达细节：`formula_1/q955`

问题问 1975 年前每年 champion 的平均完成时间秒数。Bash 把 champion 当 season points leader；Pontis 明显改进，探索了：

- `results.time`
- `positionOrder = 1`
- winner time 格式
- 每年平均秒数

Pontis 的最终 SQL 使用 race winner 逻辑，已经解决了 bash 的 DB-exploration 错误；但它仍被判 wrong，因为时间解析方式和 golden SQL 的 substring 假设不完全一致。这个 case 应该从“Pontis仍错”里单独看：Pontis 的数据库理解是明显进步的，剩余更像 SQL 表达/benchmark 风格问题。

改进方向：对复杂格式字段生成 canonical parser。比如 `HH:MM:SS.mmm` 不应该由 agent 每次手写 `instr/substr`，图谱应保存字段格式和标准转换表达式。

### 6. schema 同名列和不必要 DISTINCT/额外 join：`card_games/q406`

问题问 legal Creature cards 的 ID。Pontis 找到了 `legalities.status='Legal'` 和 `cards.types='Creature'`，但仍错：

```sql
SELECT DISTINCT c.id
FROM cards c
JOIN legalities l ON c.uuid = l.uuid
WHERE c.types = 'Creature'
  AND l.status = 'Legal'
ORDER BY c.id
```

golden 还 join 了 `rulings` 且不 DISTINCT。这里的 DB exploration 已经解决了一半：Pontis 比 bash 更接近正确语义，但没有意识到 benchmark/golden 要求的 join surface 和 multiplicity。

这个 case 边界比较微妙：`legal status` 本身通过 `legalities` 足够；golden 额外 join `rulings` 更像数据集风格/隐含约束。它不完全是图谱能独立解决的问题，但图谱至少可以提示：加入 `rulings` 会改变 multiplicity，`DISTINCT` 会改变输出 multiset。

改进方向：对候选 SQL 做 multiplicity diff：

```sql
SELECT COUNT(*) FROM cards JOIN legalities ...
SELECT COUNT(*) FROM cards JOIN rulings JOIN legalities ...
SELECT COUNT(DISTINCT id) ...
```

当 BIRD 执行评测按 multiset 比较时，DISTINCT/额外 join 都要谨慎。

## 归纳出来的 Pontis 短板

### A. 图谱有 metadata，但缺少“候选策略比较”

Pontis 经常能找到相关表列，但不会系统比较候选 SQL strategy。例如：

- direct join vs padded join
- row grain vs patient grain
- entity table direct filter vs transaction sample filter
- `WHERE remaining > 0` vs `GROUP BY HAVING SUM(remaining) > 0`
- `col < threshold` vs `CAST(col AS REAL) < threshold`

改进不只是给更多列 brief，而是让 explorer 生成可比较的候选策略和反例。

### B. row grain 没有成为一等实体

很多错误来自不知道当前表是一行一个实体、一行一次记录，还是一行一个属性历史版本：

- `Laboratory` 是多次检验记录，不是 patient 主表。
- `Player_Attributes` 是 player 的多时间点属性。
- `budget` 是 event 的类别预算行。
- `results` 是 driver-race 粒度。

图谱应该为表和 join path 标注 grain，并在 question/evidence 提到 percentage/average/count 时约束聚合口径。

### C. join coverage / lossy join 缺失

`toxicology/q306` 这类问题说明，额外 INNER JOIN 会悄悄丢行。Pontis 有时能避免，有时不能。图谱应预计算：

- A.key 在 B.key 中的覆盖率
- A LEFT JOIN B 后 missing key 示例
- join 后 row count 变化
- 一对一、一对多、多对多关系

这比单纯 schema FK 更有用，因为 BIRD 里很多关系不是严格外键。

### D. 值域 profile 还不够“可执行”

Pontis 已有 topk/sample，但缺少针对 SQL 行为的 profile：

- TEXT 数值列空串/非数值行为
- 大小写精确匹配
- LIKE vs equality 命中差异
- 日期格式歧义
- 枚举列的“直接属性”优先级

对 Text2SQL 来说，value profile 不应只是展示样本，而应给出会改变 SQL 结果的比较。

### E. final SQL 前缺少轻量 verifier

很多 Pontis 错误在日志里已经查询出正确方向，但最终 SQL 还是选错。需要一个轻量 verifier，不是 guardrail 式硬编码，而是基于当前 DB 自动检查：

- 输出行列数是否符合 question shape
- 是否用了未验证的 DISTINCT/GROUP BY/LIMIT
- 是否有候选 join path 结果冲突
- 是否有比当前列更直接的候选列
- 是否把多行记录表提升到了实体粒度

## 建议新增/优化的 explorer 模块

### 1. `join_path_profiler`

输入：候选表集合、候选 join keys。  
输出：

- join coverage
- left-only/right-only 示例
- row count before/after
- top-N result under different join path
- 是否 lossy

优先解决：`california_schools/q10`、`toxicology/q306`、`formula_1` join 粒度问题。

### 2. `row_grain_analyzer`

输入：表、主键候选、常见实体 id。  
输出：

- 表粒度：entity/event/measurement/history/bridge
- 每个实体平均行数
- 多行实体示例
- 常用聚合口径提示

优先解决：`thrombosis_prediction/q1190`、`european_football_2/q1093`、`student_club/q1447`。

### 3. `value_semantics_profiler`

输入：列。  
输出：

- enum/topk/sample
- NULL/empty/non-numeric
- exact vs LIKE 差异
- raw comparison vs CAST comparison 差异
- threshold 周边样本

优先解决：`thrombosis_prediction/q1277`、大小写/LIKE 问题、医学 TEXT 数值列。

### 4. `predicate_column_competition`

输入：question/evidence 中的 predicate phrase。  
输出：

- 候选列列表
- 每个候选列是否含目标 value
- 候选列所属表粒度
- 更具体/更直接的列优先级

优先解决：`california_schools/q4`、`debit_card_specializing/q1470`、`card_games` 多表字段歧义。

### 5. `result_shape_verifier`

输入：候选 SQL + question。  
输出：

- 实际行列数
- 是否 scalar/list/entity-list
- GROUP BY/DISTINCT/LIMIT 是否改变预期
- 多候选 SQL 的 result diff

优先解决：`formula_1/q881`、各种 percentage/average/count 误聚合。

## 优先级建议

短期最值得做：

1. `row_grain_analyzer`
2. `join_path_profiler`
3. `value_semantics_profiler`

原因是这三类覆盖了最多 Pontis 仍错的 DB-exploration-fixable case，而且是数据库内生信息，不依赖 golden SQL 或 query log。

中期再做：

4. `predicate_column_competition`
5. `result_shape_verifier`

这两类更接近 agent 决策层，需要和 prompt/guardrail 配合，但收益也很明显。

## 最终判断

如果只问“这些 bash 的 DB exploration 错题 Pontis 有没有做对”，答案是：只做对了约三分之一，仍错约三分之二。

如果问“Pontis 是否证明了图谱路线有效”，答案也是肯定的：`california_schools/q4`、`codebase_community/q576`、`debit_card_specializing/q1470`、`student_club/q1447` 都是图谱 metadata/value sketch/disambig 明显帮助 agent 超过 bash 的例子。

但现在 Pontis 的瓶颈已经不是“有没有图谱”，而是“图谱信息没有被组织成可决策的探索策略”。下一步应该把图谱从静态 metadata 升级为面向 SQL 决策的 profile：

- join 是否会丢行
- 表是什么 row grain
- 值比较在 SQLite 下怎么表现
- 候选列/候选 join path 谁更直接
- 当前 SQL 的输出形状是否符合问题

这部分不需要看 golden SQL，也不需要训练集先验，属于真正能在无知之幕下提升 zero-shot DB understanding 的方向。
