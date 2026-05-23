# Zero-shot 数据库理解错误分析

分析对象：

- Pontis no-readme 运行结果：`workspace/baselines/pontis/runtime_logs/bird_dev_bird_dev_full_noglobal_noreadme_reflection_20260523`
- Bash 运行结果：`workspace/baselines/bash_agent/runtime_logs/bird_dev_bash_agent_bird_dev_full`

这份报告刻意只关注 zero-shot 设定下的数据库理解问题。Golden SQL 只作为事后诊断参照使用。核心问题是：在“无知之幕”下，如果有更好的图谱和 explorer，能否为强模型提供足够的数据库理解，从而写出正确 SQL？

## 1. 总体对比

no-readme Pontis 运行还缺 1 个被挂起的 query（`financial/q102`），所以所有成对比较都基于 1533 道共同题。

| 指标 | 数量 |
|---|---:|
| Pontis 已完成 | 1533 |
| Pontis 正确 | 961 |
| Pontis 已完成题准确率 | 62.69% |
| Bash 已完成 | 1534 |
| Bash 正确 | 920 |
| 共同题数 | 1533 |
| 两者都对 | 835 |
| 两者都错 | 487 |
| Pontis 对、bash 错 | 126 |
| bash 对、Pontis 错 | 85 |
| Pontis 在共同题集上的净收益 | +41 |

Pontis reflection 将 279 个共同错题判为 `Database Understanding Error`。

| DB | Pontis 数据库理解错误数 |
|---|---:|
| card_games | 52 |
| formula_1 | 44 |
| toxicology | 32 |
| codebase_community | 31 |
| thrombosis_prediction | 29 |
| financial | 24 |
| california_schools | 22 |
| student_club | 16 |
| european_football_2 | 15 |
| debit_card_specializing | 11 |
| superhero | 3 |

## 2. 数据库理解错误的原因

主要 DBU 错误不是语法问题，而是没有把问题映射到正确的数据库对象、关系角色、值或行粒度。

对 279 个 DBU case 的启发式主分类如下：

| 错因 | 数量 | 图谱可改进性 |
|---|---:|---|
| 表 / 实体选择错误 | 131 | 高 |
| 列 / 来源选择错误 | 49 | 高 |
| 聚合粒度 / 公式理解错误 | 40 | 中 |
| JOIN 路径 / 关系角色错误 | 31 | 高 |
| value grounding 错误 | 19 | 高 |
| query intent 映射错误 | 6 | 中 |
| 其他 | 3 | 低 |

粗略的图谱可改进性估计：

| 类别 | 数量 | 含义 |
|---|---:|---|
| 高 | 230 | 更好的图谱元信息 / 消歧信息很可能能避免错误 |
| 中 | 46 | 图谱能提供帮助，但模型仍需要非平凡的意图推理 |
| 低 | 3 | 很可能不能仅靠数据库理解解决 |

### 表 / 实体选择错误

这是最大的一类。模型经常选择一个看起来合理的表，但数据库里有更具体的表，或者另一个表有不同的行覆盖范围。

例子：

- `formula_1/q892`：Pontis 将 `results.points` 跨比赛求和来回答 “most points scored”；golden 使用 `driverStandings.points`，这是赛季 standings snapshot。这是表粒度理解错误：单场比赛积分 vs 累计 standings 积分。
- `toxicology/q205`：Pontis 为了列出含碳分子，将 `atom` JOIN 到 `molecule`。Golden 只用 `atom`。`molecule` 表只覆盖带标签的分子，因此 JOIN 会静默丢掉合法的 molecule_id。
- `card_games/q431`：Pontis 用 `foreign_data` 处理 Japanese writing；golden 用 `set_translations`。`Japanese` 既可能指卡牌翻译，也可能指 set 翻译，图谱需要明确区分这些实体角色。
- `student_club/q1436`：Pontis 将 “links to events for members” 映射为 `expense -> budget -> event`；golden 使用 `expense -> member -> attendance -> event`。目标是成员参加活动的 link，不是 budget 指向 event 的 link。

图谱改进方向：

- 添加表粒度摘要：例如“一行是一条 race result”、“一行是某场比赛后的 standings snapshot”、“molecule 表只是 labelled subset”、“atom/bond 包含超出 labelled molecule 表的 molecule_id”。
- 添加领域短语到实体的映射：当问题问 F1 cumulative points 时，`points scored -> driverStandings.points`；`translated set name -> set_translations`；`card foreign name -> foreign_data`。
- 对 toxicology 这类 schema 添加提醒：除非输出或过滤条件需要 labelled entity 字段，否则不要 JOIN 到实体表。

### 列 / 来源选择错误

这类错误发生在多个列看起来语义相近的时候。

例子：

- `formula_1/q956`：Pontis 将 “ranked 2” 映射为 `positionOrder = 2`；golden 使用 `results.rank = 2`。两个字段都像排名字段，但含义不同。
- `card_games/q514`：混淆 `manaCost` 和 `convertedManaCost`。
- `financial/q133`：问题问 branch location 和 district name；Pontis 输出 `A3` / `A2`，而 golden 将输出理解为 `district_id` / `A2`。这部分是输出契约问题，也涉及字段角色问题。
- `california_schools/q43`：Pontis 使用 `satscores.cname` 作为 county；bash/golden JOIN 到 `schools.County`。问题问 school 位于哪里，因此 canonical location source 是 `schools`。

图谱改进方向：

- 为常见指标家族构建同名 / 同域消歧实体：`rank` vs `positionOrder`，`results.points` vs standings 中的 `points`，`manaCost` vs `convertedManaCost`。
- 为输出域标注 canonical source columns：location、district、school name、author/user display、diagnosis、date。
- query-time context 中优先展示与问题短语最相关的 top disambiguation，而不是把所有相关列一股脑 dump 出来。

### JOIN 路径 / 关系角色错误

Pontis 有时检索到了正确的表，但选择了错误的关系角色。

例子：

- `financial/q138`：“branch where crimes occurred” 应该使用 `client.district_id -> district`；Pontis 走了 `account -> disp -> client`，把 branch 理解成 account branch。图谱已经说明 `client.district_id` 和 `account.district_id` 含义不同，但模型没有把短语绑定到正确角色。
- `financial/q90`：“accounts eligible for loans” 应该 JOIN `account -> loan`；Pontis 使用 owner-disposition 存在性条件，漏掉了 loan 表。
- `codebase_community/q689`：“last to edit post ID 183” 很棘手。Pontis 使用 `LastEditorUserId`；bash 使用 `postHistory`；golden 使用 `OwnerUserId` 加 `ORDER BY LastEditDate`。这个 case 说明角色存在歧义：即使完整了解 schema，也未必能唯一预测 golden。
- `thrombosis_prediction/q1270`：Pontis 用 `LEFT JOIN Examination`，把没有 examination row 当成没有 symptom。Golden 用 `INNER JOIN`，意味着 “no symptoms” 必须是被观察到的 examination 值，而不是 absence of examination event。

图谱改进方向：

- 添加关系角色节点：owner、editor、commenter、history actor、account branch、client residence、event attendance、budget event。
- 标注负例语义：对 event / measurement 表，“没有行”不等于 negative fact。
- 对每个 bridge / event table 存储缺失行的含义：unknown、absence，还是 simply no record。

### Value grounding 错误

这类错误涉及具体值、日期格式、枚举语义，以及精确匹配 vs 模糊匹配。

例子：

- `california_schools/q39`：Pontis 将 “Fresno schools” 映射到 `City='Fresno'`；golden 使用 `County='Fresno'`。Bash 做对了。这是地名维度消歧问题。
- `formula_1/q998`：evidence 说 “91st refers to points”；Pontis 将其解释为 rank offset 91，而 golden 过滤 `driverStandings.points = 91`。
- `thrombosis_prediction/q1187`：evidence 说 examined date 指 `Date`；Pontis 仍使用 `Examination."Examination Date"` 并 JOIN `Laboratory`，错误缩小了结果集。

图谱改进方向：

- 为歧义地点词添加 value-grounding 示例：county / city / district / school district。
- evidence 短语明确命名列时，在图谱上下文里保留精确的表 / 列绑定。
- 构建 enum/value cards：低基数值、日期格式、别名，以及值是代码还是展示标签。

### 聚合粒度 / 公式错误

部分 DBU case 不只是 golden style：模型选择了错误的业务人群或测量粒度，导致语义答案变化。

例子：

- `financial/q128`：Pontis 将 “female account holders” 理解为 `disp.type='OWNER'`；golden 按 district 统计所有女性 client。这是实体定义错误。
- `financial/q130`：Pontis 统计 distinct clients；golden 统计 accounts。目标实体是 account，不是 person。
- `toxicology/q311`：Pontis 使用 molecule-level anti-joins 排除 sulphur 和 double bonds；golden 在 atom/bond JOIN 行上统计 distinct molecule IDs。这是行粒度和关系语义问题。
- `thrombosis_prediction/q1193`：Pontis 从所有 patients 出发并排除 ANA Pattern P；golden 从 examination rows 出发。被检查人群才是正确分母。

图谱改进方向：

- 对每张表存储 row grain 和 “countable entity”：patient row、examination event、lab measurement、transaction、account、client、molecule atom、molecule bond。
- 存储常见问题目标映射：“patients with lab condition” 可能按 lab rows 或 patient IDs 计数，取决于 wording/evidence；在 financial schema 中，“account holders” 经常映射为 clients，不只 OWNER dispositions。
- 当候选 query 在 measurement/event 表上添加 `DISTINCT`、`OWNER`、`LEFT JOIN` 或 anti-join 时，生成图谱级风险提示。

## 3. 更好的图谱建设能否解决这些问题？

大部分可以，但不是全部。

### 高置信可由图谱修复

这些 case 的正确选择来自数据库事实，只是这些事实需要被更好地表达：

- 表覆盖范围和行粒度事实：toxicology 的 `molecule` 是 labelled subset；`atom` / `bond` 是覆盖范围更广的来源。
- 关系角色感知 JOIN：`client.district_id` vs `account.district_id`，`OwnerUserId` vs `OwnerDisplayName`，`LastEditorUserId` vs `postHistory.UserId`。
- 指标家族消歧：F1 的 `results.points` vs `driverStandings.points`，`rank` vs `positionOrder`，lap time 来源 `lapTimes` vs `results.fastestLapTime`。
- 值 / 实体 grounding：city vs county，set translation vs card translation，lab `Date` vs examination date。

### 中等置信可由图谱修复

这些需要图谱加上更强的 query intent reasoning：

- “account holder”、“branch”、“best selling”、“oldest”、“last edited”、“examined”、“translation”、“ruling”、“connected atoms”。
- 自然语言本身没有完全指定的聚合粒度，但表粒度可以帮助确定默认解释。

### 低置信可由图谱修复

有些 case 在“无知之幕”下本身信息不唯一：

- 题目措辞支持多个合理 SQL，golden 选择其中一个，但数据库侧证据不足。
- 正确答案依赖 benchmark convention，而不是 schema understanding。
- `codebase_community/q689` 是一个很好的警示例：Pontis、bash 和 golden 都选择了不同但看似合理的 “last edited user” 路径。

## 4. Pontis 输给 Bash 的地方

共同题集中有 85 道题 bash 正确、no-readme Pontis 错误。

Pontis reflection 分类如下：

| Pontis 错误类别 | 数量 |
|---|---:|
| Golden SQL Style Error | 58 |
| Database Understanding Error | 26 |
| 缺失 reflection / parse 问题 | 1 |

所以 Pontis 输给 bash 的题，大多数仍然是 style / output-shape 问题。真正对图谱改进有用的是其中 26 个 DBU loss。

DBU loss 按 DB 分布：

| DB | 数量 |
|---|---:|
| california_schools | 7 |
| formula_1 | 6 |
| thrombosis_prediction | 4 |
| financial | 2 |
| student_club | 2 |
| toxicology | 2 |
| codebase_community | 1 |
| european_football_2 | 1 |
| superhero | 1 |

代表性 Pontis-loss DBU case：

- `california_schools/q39`：Pontis 选择 `City='Fresno'`；bash 选择 `County='Fresno'`。这正是图谱应该帮助解决的地理维度消歧。
- `formula_1/q892`：Pontis 对 `results.points` 求和；bash 使用 `driverStandings.points`。这说明 Pontis 没有充分编码 F1 指标粒度区别。
- `formula_1/q904`：Pontis 使用 `results.fastestLapTime`；bash 使用 `lapTimes.milliseconds`，后者是 lap record 的事件级来源。
- `formula_1/q956`：Pontis 使用 `positionOrder = 2`；bash 使用 `rank = 2`，匹配显式列短语。
- `thrombosis_prediction/q1187`：Pontis 使用 `Examination Date`；bash 使用 `Laboratory.Date`，匹配 evidence。
- `toxicology/q205/q225`：Pontis 不必要地 JOIN `molecule`，丢失只存在于 atom/bond 中的 molecule IDs；bash 保持在 `atom` / `bond` 上。

解释：

- Bash 经常因为更简单、更贴近原始 schema/evidence 而获胜。
- Pontis 有时过度使用图谱语义或“合理”的领域假设，添加了题目不需要的 JOIN 或过滤条件。
- 解决方案不是减少图谱，而是让图谱更锋利：角色特异、粒度特异、query intent 特异的元数据，再加上最终对 unnecessary joins 的检查。

## 5. Pontis 赢过 Bash 的地方

共同题集中，Pontis 正确而 bash 错误的题有 126 道。

Bash 失败状态：

| Bash 状态 | 数量 |
|---|---:|
| WRONG | 105 |
| PARSE_ERROR | 19 |
| EXEC_ERROR | 1 |
| ERROR | 1 |

Pontis-only wins 按 DB 分布：

| DB | 数量 |
|---|---:|
| formula_1 | 23 |
| california_schools | 16 |
| codebase_community | 16 |
| toxicology | 16 |
| card_games | 15 |
| thrombosis_prediction | 14 |
| debit_card_specializing | 6 |
| student_club | 6 |
| superhero | 6 |
| european_football_2 | 4 |
| financial | 4 |

代表性 Pontis wins：

- `california_schools/q20`：Pontis 使用 `frpm.Low Grade` 和 `frpm.High Grade`；bash 使用 `schools.GSoffered='9-12'`。Pontis 受益于 grade 字段消歧。
- `california_schools/q26`：Pontis 使用 `County='Monterey'`、`frpm.School Type` 和 FRPM count；bash 使用 city 和 school EIL 字段。Pontis 选择了更好的来源表。
- `codebase_community/q540`：Pontis JOIN `posts.OwnerUserId -> users.Id` 并过滤 `users.DisplayName`；bash 过滤 `posts.OwnerDisplayName`，后者是较弱的反规范化字段。
- `codebase_community/q569`：Pontis JOIN 所有 title 匹配的 posts 并计 votes；bash 用 `LIMIT 1` 只选一个 post。
- `debit_card_specializing/q1470`：Pontis 使用 `gasstations.Segment='Premium'`；bash 发明了 transaction/product 路径。
- `card_games/q412`：Pontis 使用精确条件 `types='Creature'`；bash 使用模糊条件 `type LIKE '%Creature%'`。
- `toxicology/q390` 类似 case：Pontis 保持所需输出列和关系行，而 bash 经常额外输出 ID 或使用 LEFT JOIN。
- `thrombosis_prediction` 中的若干 case：当相关 column brief 被检索到时，Pontis 更常能在 `Patient`、`Examination`、`Laboratory` 之间做出正确选择。

解释：

- 当图谱元数据能直接解决 schema linking 时，Pontis 获胜：table brief、column brief、relationship role、disambiguation node、value examples。
- 图谱在含有大量语义相近字段的 schema 中价值最大：`formula_1`、`card_games`、`codebase_community`、`toxicology`、`thrombosis_prediction`。
- Pontis 也受益于更多探索和验证。这是有用的，但会带来成本，并且偶尔会让模型过度相信图谱侧假设。

## 6. 建议的图谱改进

下一阶段图谱工作应针对数据库理解，而不是 golden style。

1. 添加 row-grain 和 coverage facts。
   - 对每张表标注：一行代表 entity / event / measurement / history / relationship / snapshot。
   - 显式标注 subset table，例如 `toxicology.molecule` 是 labelled subset；`atom` 和 `bond` 有更广的 molecule_id 覆盖。

2. 添加 role-aware relationship entities。
   - 编码语义角色标签：account branch、client residence、owner、editor、last editor、commenter、event attendee、event budget、transaction actor。
   - query 阶段暴露角色冲突：如果问题说 “branch”，同时展示 `client.district_id` 和 `account.district_id` 及各自适用语义。

3. 强化 column-family disambiguation。
   - F1：`rank` vs `positionOrder`，`results.points` vs `driverStandings.points`，`lapTimes.milliseconds` vs fastest-lap summary fields。
   - Card games：`type/types/subtypes/supertypes`，`manaCost/convertedManaCost`，`foreign_data/set_translations`。
   - Medical：`Patient.Diagnosis/Examination.Diagnosis`，`Laboratory.Date/Examination Date`。

4. 构建 value-grounding cards。
   - 低基数枚举值，包含精确拼写和预期的 equality/LIKE 行为。
   - 日期格式和可能的日期列。
   - 地名歧义：county/city/district/region/school district。

5. 添加 “unnecessary join risk” 信号。
   - 当候选 SQL JOIN 了一个不贡献输出、过滤条件或必要关系的表时，给出警告。
   - 这对 toxicology 尤其重要，因为 JOIN `molecule` 会丢失合法的 `atom` / `bond` molecule IDs。

6. 改进 query-intent 图谱摘要。
   - 维护可复用短语映射，不作为 test-specific rules，而作为 schema-local semantics：
     - `translation` 可能指 set translation，也可能指 card foreign data。
     - F1 中的 `points scored` 可能指 race points，也可能指 standings points。
     - `examined date` 可能指 lab measurement date，也可能指 clinical examination date。
     - `connected atoms` 可能指 bond endpoints，而不是任意 molecule membership。

## 7. 结论

在 zero-shot 框架下，Pontis 已经表现出真实系统优势：共同题集上比 bash 净多对 41 题，有 126 道 Pontis-only wins。这些 wins 主要来自更好的 schema linking、关系感知和 value grounding。

剩余数据库理解错误大多不是不可解决的 benchmark artifact。相当一部分可以通过图谱改进解决：

- 表 / 实体选择错误，
- 列 / 来源选择错误，
- JOIN 角色错误，
- value grounding 错误，
- row-grain 混淆。

当前主要弱点是：Pontis 图谱知识覆盖面很广，但还不总是 operational。它知道很多事实，但 agent 在选择 SQL 路径的关键时刻，不总能拿到那个决定性区别。下一步应该让图谱更 role-aware、更 grain-aware，并让 query-time retrieval 优先返回消歧事实，而不是泛泛的 schema summary。
