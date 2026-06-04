# Pontis BIRD README 遵守性与错因分析

分析对象：

```text
workspace/baselines/pontis/runtime_logs/20260529_211836_bird_dev_full_readme_direct_noglobal_noreflection
workspace/baselines/pontis/results/20260529_211836_bird_dev_full_readme_direct_noglobal_noreflection
```

本轮结果为 `1003 / 1534 = 65.38%`，错题 531 道。运行配置启用了 `bird_readme=true`、`use_bird_global=false`、`reflection=false`，并使用 `BirdReadmeFinalRecheck` 在 final SQL 前回灌完整 BIRD README。

## 关键结论

当前问题不是 README 没有进入 agent。逐题日志显示，`BirdReadmeFinalRecheck` 在 1532 / 1534 道题上触发，其中 529 / 531 道错题也触发了 final recheck；仅 Q1316、Q1431 因最终输出为空或异常没有进入正常 README recheck。因此，继续简单增加 README 文本或再重复一次同样的 self-check，收益预计有限。

更准确的判断是：当前机制把 README 交给同一个已经生成候选 SQL 的 agent 自查，容易受到既有解法锚定影响。它能提醒模型，但不能强制模型证明每个 `SELECT`、`WHERE`、`DISTINCT`、`ORDER BY`、`GROUP BY`、`LIMIT` 都有 question/evidence/README 授权。

同时，部分错题并不是“不遵守 README”，而是 README 规则和 BIRD gold 口径存在冲突。例如 Q6 的问题是 “list the schools”，当前 README 中“实体但未指定展示字段则输出主键/ID/code”的规则会倾向输出 `CDSCode`，但 BIRD gold 输出 `School`。这类问题需要调整 README 本身，而不是更强地执行现有 README。

## 2026-06-03 机制更新

本轮不再采用硬编码规则拦截。`BirdReadmeFinalRecheck` 已改为先把 README 中每条 `Rxx.` 规则解析成独立可检索项，再基于 question、evidence、candidate SQL、近期工具观测和通用 SQL 语法特征做关键词/语义检索，只把候选 rule cards 交给独立 reviewer 判断。retriever 中不写具体 R 编号、具体数据库、具体字段或具体题型逻辑；README 增删改规则时，召回质量应由规则文本本身承担。

小规模 smoke 结果：

| run | qids | 结果 | 说明 |
| --- | --- | --- | --- |
| `20260603_typical3_readme_schema4_smoke1` | 344, 648, 1504 | 2/3 correct | Q344、Q648 转正；Q1504 暴露 value grounding 与“0-hit 精确路径有效”的冲突。 |
| `20260603_q1504_valuefix_smoke1` | 1504 | 1/1 correct | 修改 `SQLValueGroundingCheck` 后，日期周期 `LIKE '%YYYY-MM%'` 的 0-hit 精确路径不再被强制改写，最终保留 transaction-detail 路径。 |

代表性修复：

- Q344：reviewer 现在能选中 R11，要求行级/印刷对象输出本地主键或本地行标识，而不是展示名 `name`。最终由 `SELECT c.name` 修正为 `SELECT c.id`。
- Q648：R20 强化了 `available website URL` 作为输出属性修饰词时不自动添加 `IS NOT NULL`，最终保留 NULL 行并转正。
- Q1504：schema challenge 明确禁止仅因明细事实表 0-hit 就改用非零命中的月度汇总代理表；`SQLValueGroundingCheck` 允许日期/时间列的周期过滤返回空结果。最终选择 `transactions_1k` 路径，执行评测转正。

仍需注意：旧版“确定性 SQL 契约 lint”建议不再作为当前优先方案。硬拦截容易把 README 写死进代码，也会在 README 增删改时失效。当前方向是：规则内容保留在 README，retriever 只做通用检索，reviewer 只判断检索到的候选规则；DB exploration consistency 则通过 schema challenge / judge 阶段比较候选路径、行粒度、字段语义、0-hit 精确路径和代理表风险。

## 总体错因分布

下面是基于 `results.jsonl` 的静态 SQL 差异归因。分类是启发式的，主要用于定位可修复方向；同一道题可能同时有多个 SQL 差异，表中采用主类归因。

| 主类 | 错题数 | 占错题比例 | 说明 |
| --- | ---: | ---: | --- |
| README / 输出契约类 | 262 | 49.3% | `SELECT` 输出列数、`DISTINCT`、额外 `ORDER BY/LIMIT`、百分比标度、`ROUND` 等与 BIRD 输出契约不一致 |
| 排名 / 聚合 / 公式类 | 101 | 19.0% | `COUNT/SUM/AVG/MAX/MIN`、`GROUP BY`、top-N、公式层级或是否聚合判断错误 |
| 语义歧义或隐藏先验类 | 96 | 18.1% | SQL 结构接近，但 BIRD gold 依赖不稳定口径、题目歧义或数据集特殊偏好 |
| schema / value grounding 类 | 70 | 13.2% | 表列来源、join path、过滤值、隐藏过滤或缺失过滤错误 |
| 执行异常 | 2 | 0.4% | final SQL 为空或 tokenization 错误 |

常见 SQL 差异标签：

| 标签 | 次数 |
| --- | ---: |
| table / join path 不同 | 165 |
| 聚合函数不同 | 152 |
| 额外 `DISTINCT` | 100 |
| `SELECT` 列数不同 | 98 |
| 没有明显简单 clause 差异 | 95 |
| 缺少 `DISTINCT` | 48 |
| 额外 `WHERE` 过滤 | 44 |
| 缺少 `LIMIT` | 43 |
| 额外 `GROUP BY` | 40 |
| 缺少 `ORDER BY` | 37 |
| 额外 `ORDER BY` | 24 |
| 额外 `ROUND` / 格式化 | 23 |
| 公式算术结构不同 | 20 |
| 百分比标度不同 | 14 |

## 数据库分布

错题最多的数据库是 `formula_1`、`thrombosis_prediction`、`card_games`、`toxicology`。其中 README / 输出契约类在 `thrombosis_prediction`、`card_games`、`formula_1`、`toxicology` 中尤其集中。

| 数据库 | 错题数 | README / 输出契约 | 排名 / 聚合 / 公式 | 语义 / 隐藏先验 | schema / value |
| --- | ---: | ---: | ---: | ---: | ---: |
| formula_1 | 81 | 41 | 21 | 8 | 11 |
| thrombosis_prediction | 78 | 44 | 10 | 15 | 9 |
| card_games | 74 | 42 | 16 | 9 | 7 |
| toxicology | 58 | 39 | 4 | 6 | 9 |
| codebase_community | 55 | 20 | 12 | 13 | 10 |
| european_football_2 | 46 | 23 | 12 | 10 | 1 |
| california_schools | 38 | 11 | 6 | 14 | 7 |
| financial | 34 | 17 | 4 | 6 | 7 |
| student_club | 30 | 7 | 9 | 8 | 4 |
| superhero | 19 | 8 | 4 | 6 | 1 |
| debit_card_specializing | 18 | 10 | 3 | 1 | 4 |

## 代表性案例

### 1. README 已回灌，但 agent 保留了未授权的 `DISTINCT`

Q9：

```text
Question: Among the schools with the average score in Math over 560 in the SAT test,
how many schools are directly charter-funded?
Gold: SELECT COUNT(T2.`School Code`) ... frpm ... `Charter Funding Type` = 'Directly funded'
Pred: SELECT COUNT(DISTINCT sc.CDSCode) ... schools ... sc.Charter = 1
```

日志显示 agent 读到了 charter disambiguation，也触发了 `BirdReadmeFinalRecheck`。但 final SQL 仍然保留 `COUNT(DISTINCT ...)`，并且把 “directly charter-funded” 错映射为 `schools.Charter = 1`，没有使用 `frpm.Charter Funding Type = 'Directly funded'`。这里同时包含两个问题：一是 README 对 `DISTINCT` 的约束没有真正强制执行；二是 schema grounding 选错了字段。

### 2. “How many” 被错误解释成求和

Q53：

```text
Question: How many test takers are there at the school/s whose mailing city address is in Fresno?
Gold: SELECT T1.NumTstTakr ...
Pred: SELECT SUM(s.NumTstTakr) ...
```

README 已经回灌，但模型仍把逐校的 `NumTstTakr` 指标列聚合成总和。BIRD gold 的口径是返回每个匹配学校的 test taker 数，而不是总人数。这个错误说明 README 中“question 要 list/show/return 具体字段值时保持逐行输出”还不够覆盖 `How many ... at the school/s` 这种 BIRD 风格表达，需要在 README 或 reviewer 中单独加入“表中已有指标列时，优先输出指标列，不自动 SUM”的更强规则。

### 3. 额外角色槽位和排序没有被 README 压住

Q54：

```text
Question: Please specify all of the schools and their related mailing zip codes
that are under Avetik Atoian's administration.
Gold: WHERE AdmFName1 = 'Avetik' AND AdmLName1 = 'Atoian'
Pred: WHERE (AdmFName1/LName1) OR (AdmFName2/LName2) ORDER BY School
```

agent 查询发现 Avetik Atoian 出现在多个管理员槽位，于是扩展到 `AdmFName2/AdmLName2`，并额外加了 `ORDER BY School`。README 回灌后仍然保留这些扩展。这里的问题是 BIRD gold 只接受第一个 administrator 槽位，当前 README 中“多个同构列或候选槽位，只有 question/evidence 要求全部槽位时才 UNION；否则使用被指向的代表列”的规则还没有被强制执行。

### 4. README 规则本身可能过度修正

Q6：

```text
Question: Among the schools with the SAT test takers of over 500, please list the schools
that are magnet schools or offer a magnet program.
Gold: SELECT T2.School ...
Pred: SELECT s.CDSCode ...
```

这个错误不是 agent 没遵守 README，反而更像是 README 的“实体但未指定展示字段则输出主键/ID/code”规则过强。BIRD 中 “list schools” 经常以学校名作为默认输出，而不是学校 ID。若继续强制执行这条规则，可能会增加这类错题。

### 5. 正常 final 输出缺失

Q1316、Q1431 的 final SQL 为空，日志里出现 tokenization 错误：

```text
ERROR: TokenError: Error tokenizing ...
```

这类问题与 README 遵守关系不大，应按执行稳定性或输出解析问题处理。

## 如何更强制地遵守 BIRD README

### 优先级 1：启用独立 rule reviewer，而不是同一 agent 自查

仓库中已经有 `Pontis/agent/guardrail/rule_review.py`，但当前 `agent/guardrail/__init__.py` 没有注册它，`BIRD_BENCHMARK_GUARDRAILS` 也没有启用它。建议将 `RuleComplianceReview` 注册成 `rule_review` guardrail，并在 BIRD benchmark 中放到 `bird_readme_final_recheck` 之后。

推荐顺序：

```text
round_limit
exploration_check
sql_check
bridge_check
disambig_check
value_grounding_check
bird_readme_final_recheck
rule_review
```

理由：`BirdReadmeFinalRecheck` 只是把 README 交还给主 agent；`RuleComplianceReview` 是独立 reviewer，可以要求候选 SQL 对照规则重新审查，并且会在 block 消息中给出具体违规点。对于 Q9、Q53、Q54 这类错误，独立 reviewer 比自查更有机会拦住 `DISTINCT`、错误聚合、额外槽位和额外排序。

### 优先级 2：增加确定性 SQL 契约 lint

对最常见的 README 类错误，不必完全依赖 LLM reviewer。可以在 final SQL 前增加轻量 deterministic lint，只检查高置信规则：

- 若 SQL 含 `DISTINCT`，但 question/evidence 不含 `unique`、`distinct`、`different`、`each different`，block。
- 若 SQL 含 `ORDER BY`，但 question/evidence 不含 `highest`、`lowest`、`top`、`bottom`、`rank`、`latest`、`earliest`、`order`，block。
- 若 SQL 含 `LIMIT`，但 question/evidence 不含 top-N、highest/lowest、first/last、latest/earliest、rank 类触发词，block。
- 若 SQL 含 `ROUND`、`printf`、`* 100`，但 question/evidence 没有小数位、格式化、percentage 或 `* 100` 要求，block。
- 若 SQL 含 `SUM/AVG/COUNT/GROUP BY`，但 question/evidence 没有 total/how many/average/per/group/most/common 等聚合触发词，block。
- 若 SQL 输出列数量明显多于 question 中要求的属性数量，交给 LLM reviewer 二次确认。

这种 lint 不需要知道 gold SQL，适合在 benchmark 运行时使用。它应该只覆盖高置信规则，避免把本来正确的复杂 SQL 拦掉。

### 优先级 3：把 README 改成“规则 + 反例”而不是纯规则表

当前 README 规则长而全，但 agent 在 final 阶段容易只保留大意。建议给最常错的规则增加短反例：

- 未要求 unique 时，`COUNT(DISTINCT id)` 是错的。
- “how many test takers at the schools” 如果表中已有 `NumTstTakr` 指标列，通常输出该列，不自动 `SUM`。
- “under X's administration” 默认使用被问题直接指向的管理员槽位，不自动扩展所有同构 administrator slots。
- “list schools” 在 BIRD 中通常输出 `School` / `School Name`；只有题目要求 code/id 或问题目标是抽象实体且无名称列时才输出 ID。

尤其需要修正当前 README 的实体默认输出规则。建议改为：真实世界实体存在规范名称列且问题使用复数实体名作输出目标时，默认输出名称；只有题目要求 code/id，或实体没有自然名称列时，才输出稳定 ID/code。

### 优先级 4：改成 contract-first 生成

在 final SQL 之前要求 agent 先形成一个结构化契约，然后 SQL 必须逐项满足契约：

```json
{
  "select": [{"expr": "...", "authorized_by": "question|evidence|readme"}],
  "where": [{"expr": "...", "authorized_by": "..."}],
  "join": [{"expr": "...", "authorized_by": "schema|question|evidence"}],
  "group_by": [],
  "order_by": [{"expr": "...", "authorized_by": "question"}],
  "limit": {"expr": "LIMIT 1", "authorized_by": "question"}
}
```

这一步可以不进入最终答案，但可以作为 guardrail 审查输入。它比“读一遍 README 后自己决定”更容易被程序检查。

## 建议的实验顺序

1. 先在当前 531 道错题中抽取 README / 输出契约类的 262 道，启用 `rule_review` 后重跑，观察能否提升。
2. 再加入 deterministic lint，重点看 `EXTRA_DISTINCT`、`SELECT_COLUMN_COUNT_DIFF`、`EXTRA_ORDER_BY`、`EXTRA_ROUND_FORMAT` 是否下降。
3. 修改 README 中“实体默认输出 ID/code”的规则后，重跑 `california_schools` 和 `card_games` 子集，确认不会损害已有正确题。
4. 最后再考虑 full run。直接全量跑成本较高，而且很难定位是哪一项改动生效。

## README 与 BIRD gold 的明显冲突

进一步对照当前 README 与 BIRD dev gold SQL，可以看到若干规则本身需要修正。也就是说，不能只“更强制地执行 README”，否则会把一部分本来可以接近 gold 的答案推远。

### 1. “实体未指定展示字段则输出 ID/code”过强

当前 README 写法：

```text
question 要实体但未指定展示字段 -> 输出该实体的主键、ID、code 或稳定标识。
Name/List/Find all [entity] 中的 Name/List/Find 是句首命令动词 -> 不算要求 name/title/text/label/description 字段。
```

这个规则与 BIRD gold 明显冲突。BIRD 中很多 `list schools`、`list cards`、`list teams` 实际输出的是名称列，而不是 ID/code。启发式扫描中，gold 在 `list/show/give/find/return + 实体` 场景下输出 name/title/school 等展示列的样例约 63 道。

典型例子：

```text
Q6: please list the schools that are magnet schools ...
Gold: SELECT T2.School ...
Pontis: SELECT s.CDSCode ...
```

```text
Q342: List the card names ...
Gold: SELECT name FROM cards ...
```

```text
Q353: List all the sets available in Italian translation.
Gold: SELECT T1.name, T1.totalSetSize ...
```

建议把该规则改为：

```text
若问题要求输出真实世界实体，且该实体表存在明显名称/标题列（School/name/title/player_name/team_long_name 等），BIRD 默认优先输出名称列。只有 question/evidence 明确要求 id/code，或实体没有稳定名称列，或任务对象是 print/card id、record id、submission id 等记录标识时，才输出主键、ID 或 code。
```

### 2. “未要求 unique/distinct 不加 DISTINCT”过强

当前 README 写法：

```text
question/evidence 未要求 unique/distinct/different -> 不自动加 DISTINCT。
不要为了“同一个实体身份”“同名实体”“更像自然语言列表”而添加 DISTINCT。
```

这个规则也与 BIRD gold 冲突。扫描中，gold SQL 含 `DISTINCT` 但 question/evidence 没有显式 `distinct/unique/different` 等词的样例约 217 道。BIRD 经常用 `DISTINCT` 消除 join 到 legalities、translations、rulings、历史记录、桥表后产生的重复展示值。

典型例子：

```text
Q344: List all the mythic rarity print cards banned in gladiator format.
Gold: SELECT DISTINCT T1.id ...
```

```text
Q355: What is the keyword found on card 'Angel of Mercy'?
Gold: SELECT DISTINCT keywords FROM cards ...
```

```text
Q373: Name the cards that were illustrated by Aaron Boyd.
Gold: SELECT DISTINCT name FROM cards WHERE artist = 'Aaron Boyd'
```

建议改成：

```text
默认不为了美化结果添加 DISTINCT；但当 JOIN 到一对多辅助表、翻译表、合法性表、rulings、历史表或桥表会产生重复的同一输出值，而 question 要的是属性值或实体集合时，可以使用 DISTINCT。COUNT(DISTINCT ...) 仍需更谨慎，只有统计唯一实体/唯一记录时使用。
```

也就是说，`SELECT DISTINCT value` 应比 `COUNT(DISTINCT id)` 更容易被允许。

### 3. “How many”不总是聚合

当前 README 写法整体倾向于：

```text
question 要 total/how many/average/per group/most common -> 使用对应聚合或分组。
```

但 BIRD 里不少 “How many ...” 问的是表中已有的指标列，而不是要求对行做 `COUNT` 或 `SUM`。扫描中这类明显样例约 15 道。

典型例子：

```text
Q53: How many test takers are there at the school/s whose mailing city address is in Fresno?
Gold: SELECT T1.NumTstTakr ...
Pontis: SELECT SUM(s.NumTstTakr) ...
```

```text
Q72: How many students ... are enrolled ...
Gold: SELECT T1.`Enrollment (Ages 5-17)` ...
```

```text
Q572: How many views did the post titled ... get?
Gold: SELECT ViewCount FROM posts ...
```

```text
Q543: ... how many answers did it get?
Gold: SELECT MAX(T1.AnswerCount) ...
```

建议补充：

```text
`How many` 只有在问题要求统计候选行/实体数量时才对应 COUNT/SUM。若 schema 中存在与问题直接同义的预计算指标列，如 NumTstTakr、Enrollment、ViewCount、AnswerCount、CommentCount、baseSetSize 等，且问题问的是某个对象“有多少/得到多少/包含多少”，优先输出该指标列，不自动 SUM 或 COUNT。
```

### 4. “不显式要求就不加 IS NOT NULL”过强

当前 README 写法：

```text
输出属性可能为 NULL -> 只有 has/with/non-null/available 等存在性条件明确要求时过滤 NULL；单纯输出该属性时保留 NULL 行。
```

BIRD gold 有一批隐式 `IS NOT NULL`，尤其在按 nullable 指标排序取 top/bottom 时，会先过滤 NULL。扫描中这类样例约 19 道。

典型例子：

```text
Q915: Which country is the oldest driver from?
Gold: SELECT nationality FROM drivers WHERE dob IS NOT NULL ORDER BY dob ASC LIMIT 1
```

```text
Q927: Which driver created the fastest lap speed ...
Gold: ... WHERE T2.fastestLapTime IS NOT NULL ORDER BY T2.fastestLapSpeed DESC LIMIT 1
```

```text
Q357: What type of promotion is of card 'Duress'?
Gold: SELECT promoTypes FROM cards WHERE name = 'Duress' AND promoTypes IS NOT NULL
```

建议改成：

```text
单纯列表查询不因输出列 nullable 自动过滤 NULL；但当 nullable 列参与 ORDER BY / top-bottom / oldest-youngest / fastest-slowest，或问题目标就是某个属性值且 NULL 表示未知而非有效答案时，可以添加 IS NOT NULL。
```

### 5. 管理员槽位规则不是 README 冲突，而是执行不够强

Q54、Q85、Q86 这类题中，gold 默认使用 `AdmFName1/AdmLName1`，不扩展到 `AdmFName2/AdmLName2/AdmFName3/AdmLName3`。这与 README 中“多个同构列或候选槽位，只有 question/evidence 要求全部槽位时才 UNION/扩展”的规则基本一致。

因此这里不是 README 与 gold 冲突，而是主 agent 在看到数据库中其他槽位也有匹配值后，覆盖了 README 的约束。此类问题适合交给 rule reviewer 或 deterministic lint 强制拦截。

### 6. 排序和 LIMIT 大多不是冲突

粗扫时看起来有少量 gold `ORDER BY/LIMIT` 没有 top/maximum 等显式词，但人工看多数其实有自然语言触发词，例如 `fewest`、`biggest`、`older`、`fastest`、`costs more`。这些与 README 的 top/bottom/ranking 规则不冲突，只需要扩大触发词表，而不是修改原则。

## 2026-06-03 schema challenge + retrieved README reviewer smoke

本轮验证的关键机制变化：

- `bird_readme_rule_retriever.py` 只从当前 README 文本解析 `R数字.` 规则，并用 question/evidence/candidate SQL/recent context 做关键词和语义召回；不包含具体规则、数据库字段、qid 或 SQL 特征硬编码。
- `BirdSchemaChallengeController` 的 judge SQL 不再直接释放，而是先要求主 agent 原样输出 selected SQL，使下一轮 `BirdReadmeFinalRecheck` 必定能检查。
- `BirdReadmeFinalRecheck` 优先从当前 assistant response 提取 SQL，再回退到历史消息提取，避免 context rewrite 后检查旧 SQL 或漏检。

典型错题结果：

- Q359 `card_games`: reviewer 现在能召回并执行 R76，成功拦截 `JOIN sets + ORDER BY releaseDate + LIMIT 1` 这类由 evidence-mapped phrase 二次推导出的行选择，并要求删除。主 agent 执行后输出 `SELECT originalType FROM cards WHERE name = ...`。仍未对齐 gold，因为 gold 额外使用 `originalType IS NOT NULL`；这暴露 R20/R76 之后还缺少“输出属性为 NULL 占位行时是否过滤 NULL”的 BIRD gold-style 规则。目前不能通过硬拦截或 retriever 解决。
- Q584 `codebase_community`: schema challenge 已经比较 `comments.Text`、`comments.PostId=目标帖子`、`postHistory.Comment` 等路径，但仍选择“编辑过该帖的用户留下的全局 comments.Text”。Gold 使用 `postHistory.Comment`。这不是 final README reviewer 的职责；属于 schema/gold 语义偏好问题，需要在数据库探索一致性或多候选 judge 的 BIRD 风格先验中处理。
