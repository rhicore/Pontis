# Pontis 对 DB_EXPLORATION_FIXABLE 错误的覆盖情况调研

调研对象：

- Bash Agent reflection run: `workspace/baselines/bash_agent/runtime_logs/bird_dev_20260524_031500_bash_agent_bird_dev_full_reflection_w50_fixed`
- Pontis zero-shot/no-readme run: `workspace/baselines/pontis/runtime_logs/bird_dev_bird_dev_full_noglobal_noreadme_reflection_20260523`

本文只关注 Bash Agent reflection 判定为 `DB_EXPLORATION_FIXABLE` 的错题。这个集合的含义是：在无知之幕下，只看当前 question/evidence/schema/full DB contents，就应该能通过更充分的数据库探索写出正确 SQL，不需要看其他 query-SQL pair，也不需要数据集先验。

## 总体结论

Bash Agent 当前有效识别出 `144` 个 `DB_EXPLORATION_FIXABLE` case，约占 BIRD dev 1534 题的 `9.4%`，和之前预估的 9%/150 题量级基本一致。

进一步严格复审后，需要修正这个结论：这 `144` 个是 Bash reflection 的宽口径标签，不全是“真正在无知之幕下只靠数据库探索就能唯一修正”的 case。严格判据应是：

1. question/evidence 给出目标后，当前数据库的 schema、值域、样例、join coverage 或 row grain 能明确排除错误 SQL。
2. 修正不依赖 golden SQL 的隐含偏好、其他 query-SQL pair、数据集风格先验。
3. 一个没有看过 golden SQL 的强模型，在充分探索当前 DB 后，也应该更倾向于修正后的 SQL，而不是更倾向于原 SQL。

按这个判据，`144` 个里约 `112` 个可以暂时保留为严格意义上的 `DB_EXPLORATION_FIXABLE`；约 `32` 个应改判为 `DATASET_PRIOR_REQUIRED`、输入证据/benchmark 契约问题，或输出形状/风格问题。这个数字是保守复审的第一版，不是新的自动分类结果。

### 严格保留的 DB_EXPLORATION_FIXABLE

这些 case 的共同点是：数据库本身能提供决定性反证，例如真实列名、真实枚举值、缺失/重复实体、join 后丢行、日期/数值字段行为、表粒度等。

| DB | qid |
|---|---|
| `california_schools` | q4, q20, q26, q42, q43, q51, q65, q74, q80 |
| `card_games` | q348, q382, q384, q398, q403, q407, q408, q410, q433, q438, q444, q446, q448, q449, q453, q465, q480, q482, q486, q511, q514 |
| `codebase_community` | q569, q576, q584, q587, q635, q639, q652, q656, q662, q667, q672, q673 |
| `debit_card_specializing` | q1470, q1479, q1498, q1500, q1502, q1504, q1517, q1529 |
| `european_football_2` | q1037, q1041, q1060, q1093, q1108, q1118, q1120, q1121, q1132, q1148 |
| `financial` | q124, q169, q182 |
| `formula_1` | q849, q851, q860, q861, q871, q892, q894, q897, q922, q949, q951, q964, q974, q975, q986, q1012, q1017 |
| `student_club` | q1360, q1387, q1418, q1419, q1422, q1434, q1447 |
| `superhero` | q720, q741, q803 |
| `thrombosis_prediction` | q1186, q1187, q1202, q1233, q1264, q1269, q1271, q1275, q1276, q1296, q1309 |
| `toxicology` | q215, q217, q223, q224, q257, q275, q281, q307, q321, q330, q334 |

### 应剔除或降级的 case

这些不应再被当成纯数据库探索上限。它们的问题通常是：数据库内存在多个合理解释，golden 选择了其中一种；或者模型的 SQL 在真实业务语义上反而更合理；或者错误主要来自输出列、拼接、ROUND、DISTINCT、multiset 等 benchmark 风格。

| DB | qid | 降级原因 |
|---|---|---|
| `california_schools` | q10, q19 | `satscores.cds` 补零后 join `frpm.CDSCode` 在真实数据库语义上并非荒谬；只看 DB 甚至可能支持补零。golden 偏好 direct join，是 benchmark join 契约。 |
| `card_games` | q393 | 预测额外加 `hasFoil=1` 从自然语言看更精确；golden 省略该条件，属于 benchmark 契约。 |
| `card_games` | q406 | Pontis 找到 legal Creature 的核心语义；golden 额外 join `rulings` 且保留 multiplicity，更像 multiset/benchmark surface。 |
| `codebase_community` | q694 | commenter vs post owner/display name 的语义边界不靠 DB 唯一决定。 |
| `financial` | q110 | 问题日期 `1998/9/2` 和 golden 日期 `1997-08-20` 无法由 DB-only 唯一推出。 |
| `formula_1` | q866, q881, q955, q1011 | 多数是 full name 输出、scalar vs per-driver、时间解析字段、是否返回 driverId 等输出/证据契约问题；例如 q1011 中 DB 的 `milliseconds` 反而是更自然的时间字段，golden 要解析 `time` 文本。 |
| `student_club` | q1376, q1450, q1456, q1458 | per-row ratio、income>40、full_name 两列 vs 拼接、`COUNT(position='Member')` 等主要来自 evidence/benchmark 表达习惯，不是 DB-only 唯一事实。 |
| `superhero` | q772, q812 | evidence 要返回 ID 或 `superhero_name`，但自然语言/DB 语义可支持颜色名或 `full_name`。 |
| `thrombosis_prediction` | q1190, q1218, q1223, q1241, q1245, q1267, q1277, q1292 | 多数是 row count vs distinct patient、TEXT 数值清洗、normal/abnormal 阈值解释等。只看 DB 常常会支持更“干净”的写法，不能算纯探索失败。 |
| `toxicology` | q225, q254, q263, q267, q298, q306, q317, q332 | `molecule` 表缺失的 orphan molecule 是否应计入、按 atom row 还是 molecule 计数、输出 one-row-per-element 还是 group_concat，在库内并不总是唯一；且 q225/q332 与 q267 的 orphan 处理方向本身不一致。 |

因此，后续讨论“图谱建设还能吃掉多少 DB understanding error”时，更合理的上限不是 144 个，而应先看严格保留的约 112 个；其中 Pontis 已做对和仍错的比例还需要按这个新集合重新统计。

按严格集合重新和 Pontis `bird_dev_bird_dev_full_noglobal_noreadme_reflection_20260523` 对齐：

| 指标 | 数量 |
|---|---:|
| 严格 DB 探索可修正错例 | 112 |
| Pontis 同题做对 | 44 |
| Pontis 同题仍错 | 68 |
| Pontis 修复率 | 39.3% |

被剔除的 32 个 case 中，Pontis 只有 3 个做对、29 个仍错。这说明之前“Pontis 仍错 97 个”的说法有明显混杂：里面约三分之一并不是纯 explorer 上限，而是 benchmark 契约、输入证据公式、输出形状或 golden 风格问题。真正应该拿来死磕图谱/explorer 的，是这 112 个里的 68 个 Pontis 仍错 case。

严格集合下分库统计如下：

| DB | 严格错例 | Pontis做对 | Pontis仍错 | Pontis修复率 |
|---|---:|---:|---:|---:|
| `california_schools` | 9 | 6 | 3 | 66.7% |
| `card_games` | 21 | 8 | 13 | 38.1% |
| `codebase_community` | 12 | 6 | 6 | 50.0% |
| `debit_card_specializing` | 8 | 4 | 4 | 50.0% |
| `european_football_2` | 10 | 2 | 8 | 20.0% |
| `financial` | 3 | 1 | 2 | 33.3% |
| `formula_1` | 17 | 4 | 13 | 23.5% |
| `student_club` | 7 | 1 | 6 | 14.3% |
| `superhero` | 3 | 1 | 2 | 33.3% |
| `thrombosis_prediction` | 11 | 5 | 6 | 45.5% |
| `toxicology` | 11 | 6 | 5 | 54.5% |

旧的 144 宽口径统计只作为反思分类误差的来源保留在上面的降级表中；后续案例和优化建议统一使用严格 112 口径。

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

## Pontis 仍未修正的类型：严格 112 口径

下面不再讨论原始 144 个宽口径 case，而只讨论严格保留的 `112` 个 `DB_EXPLORATION_FIXABLE`。其中 Pontis 已做对 `44` 个，仍错 `68` 个。这里的 68 个才是当前最适合用来优化 explorer/图谱的对象。

严格 68 个 Pontis 仍错 case 分布如下：

| DB | Pontis仍错 qid |
|---|---|
| `california_schools` | q43, q51, q80 |
| `card_games` | q398, q403, q408, q433, q438, q444, q446, q448, q465, q482, q486, q511, q514 |
| `codebase_community` | q584, q635, q639, q652, q656, q672 |
| `debit_card_specializing` | q1498, q1504, q1517, q1529 |
| `european_football_2` | q1037, q1041, q1060, q1093, q1118, q1120, q1121, q1148 |
| `financial` | q169, q182 |
| `formula_1` | q849, q851, q860, q861, q892, q897, q922, q949, q951, q974, q975, q986, q1012 |
| `student_club` | q1360, q1387, q1418, q1419, q1422, q1434 |
| `superhero` | q720, q741 |
| `thrombosis_prediction` | q1186, q1187, q1233, q1264, q1269, q1271 |
| `toxicology` | q215, q217, q257, q281, q330 |

这些 case 的主因可以压缩成四类。这里的分类是按主因归类，少数 case 有交叉。

| 主因类别 | 估计数量 | 说明 |
|---|---:|---|
| 表/列/谓词落点错误 | 31 | 找到了相关对象，但没有落到真正承载语义的表、列或枚举值上。 |
| row grain / 聚合口径错误 | 15 | 表粒度、实体粒度、历史记录粒度、明细行粒度混淆。 |
| 值、操作符、格式语义错误 | 12 | 大小写、日期、时间字符串、阈值、单位、枚举值映射错误。 |
| join path / coverage / date ownership 错误 | 10 | join path 会丢行、跨表借错时间戳、或没有验证候选 join 路径。 |

和旧版 97 个 Pontis 仍错归类相比，已经剔除了这些不应再作为 explorer 失败讲解的 case：

- `california_schools/q10/q19`：SAT-FRPM 补零 join 属于 benchmark join 契约，不是 DB-only 唯一事实。
- `thrombosis_prediction/q1190/q1267/q1277` 等：distinct patient、TEXT 数值清洗、row count vs patient count 的取舍常常需要数据集口径。
- `toxicology/q225/q267/q306/q317/q332` 等：orphan molecule 是否计入、one-row-per-element vs group_concat、atom row vs molecule count 在库内不总是唯一。
- `formula_1/q955/q1011`、`card_games/q406`、`student_club/q1456`：主要是输出形状、multiset、字段表达式或 benchmark surface。

## 类别一：表/列/谓词落点错误

这类是严格 68 中最大的一类。数据库中存在决定性线索：某个短语应该落到另一张表、另一个同名列、或另一个实体层级。当前 Pontis 往往能找到候选区域，但没有系统比较候选列的语义角色。

### `card_games/q408`: `cards.text` vs `rulings.text`

问题问：unknown power cards contain info about the triggered ability。Pontis 预测类似：

```sql
SELECT COUNT(*)
FROM cards
WHERE (power IS NULL OR power = '*')
  AND text LIKE '%triggered ability%'
```

错误点是 `contain info about ...` 在这个库里更应落到 `rulings.text`，而不是 `cards.text`。`cards.text` 是卡牌规则文本，`rulings.text` 是裁定/说明文本。只要 explorer 系统比较两个 `text` 列的表语义、样例和命中结果，就能排除当前选择。

需要的图谱 profile：

```text
PredicateLandingProfile
phrase: information / ruling / triggered ability
candidates:
  rulings.text: 裁定和说明文本，命中 triggered ability
  cards.text: 卡牌规则文本，语义相近但不是 ruling/info 表
hint: info/ruling/explanation phrase favors rulings.text
```

### `card_games/q433/q465`: card-level translation vs set-level translation

这两题都暴露 `language` 的实体层级问题。`foreign_data.language` 是 card-level foreign printing；`set_translations.language` 是 set-level translation。问题出现 `set of cards`、`version of a set`、或通过 `setCode` 聚合时，应该优先探索 `sets/set_translations`。

这不是硬编码 BIRD，而是图谱应记录同名字段所属的主实体层级：

```text
language candidates:
  foreign_data.language: card/printing-level
  set_translations.language: set-level
join anchors:
  cards.uuid -> foreign_data.uuid
  cards.setCode -> set_translations.setCode
```

### `student_club/q1418/q1419/q1422`: `event.type` vs `budget.category`

问题都问 event 的 category。Pontis 多次选 `event.type`，但数据库中真正叫 `category` 的列在 `budget.category`，并通过 `budget.link_to_event` 连接到 event。

这个错误可以通过列名、样例和值域直接修正：

- `event.type`: `Meeting`, `Election`, `Guest Speaker`，是活动类型。
- `budget.category`: `Food`, `Parking`, `Advertisement`, `Speaker Gifts`，是题目要的 category。

需要的 explorer 行为不是只找 `event` 表，而是在目标词 `category` 出现时搜索所有候选列，并展示值域差异。

### `formula_1/q849/q851/q922`: 同名实体属性选择错误

`formula_1` 中很多表都有 `url`、`name`、`number`、`id`。例如 q849 问 Circuit de Barcelona-Catalunya 的 introduction，Pontis 选了 `races.url`，但目标实体是 circuit，应选 `circuits.url`。q861 类似，driver number 和 qualifying number 都存在，但题目语义落在 qualifying entry。

需要的 profile 是 owning entity：

```text
url candidates:
  circuits.url: circuit introduction
  races.url: race page
  drivers.url: driver page
```

### `thrombosis_prediction/q1186/q1233/q1264/q1271`

这些是医学库里典型的表/列/值落点错误：

- q1186: diagnosis 和 examination date 应来自 `Examination`，不是 `Patient.Description`。
- q1233: “first recorded” 应落到 `Patient."First Date"`，不是 `Description`。
- q1264: APS diagnosis 在 `Patient.Diagnosis` 可直接命中，不应先绕到 `Examination`。
- q1271: `SSA` 实际 normal 值是 `negative`/`0`，不是 evidence 文本里的 `-`/`+-`。

这类 case 非常适合 `value_semantics_profiler` 和 `predicate_column_competition` 结合：先找候选列，再验证目标值是否真实存在。

## 类别二：row grain / 聚合口径错误

这类错误不是找不到列，而是不知道一行代表什么。严格集合里保留的 grain 错误，必须满足“数据库探索能够明确显示当前 grain 会导致错误”。

### `debit_card_specializing/q1498`: customer-month vs month

Pontis 用单行 `MAX(Consumption)` 找 highest monthly consumption，但 `yearmonth` 的一行是 `CustomerID x Date`，不是一个月份。要问某年最高 monthly consumption，应先按 `Date` 汇总，再取最大。

图谱应记录：

```text
yearmonth row grain: customer-month
Date alone is not unique
monthly total requires GROUP BY Date SUM(Consumption)
```

### `european_football_2/q1093/q1037/q1148`

`Player_Attributes` 是球员历史属性表，一名球员有多行属性快照。Pontis 如果直接在 attribute rows 上 `AVG` 或 `COUNT`，很容易把历史记录当成球员实体。

需要的 row grain profile：

```text
Player_Attributes grain: player_api_id x date
Player grain: player entity
aggregation warning: AVG(overall_rating) over Player_Attributes is snapshot-weighted
```

### `superhero/q720/q741`: physical hero row vs name-level entity

`superhero.id` 是物理行，但 `full_name` / `superhero_name` 有重复。q720/q741 的 question 和 evidence 都把 name 当成聚合对象，数据库也能显示重复名会改变结果，例如 `Captain Marvel` 多个 id 的 powers 合并后超过单个 hero id。

这类不是 benchmark 风格问题，因为 DB 里重复 name 的反例足够强，能够解释为什么按 id 分组会漏答案。

### `student_club/q1360`

“total budget” 应该是 `SUM(budget.amount)`，不是 budget rows 的 `COUNT(*)`。数据库样例直接显示 `amount` 是预算金额，COUNT 会得到荒谬百分比。

这类可以通过 result sanity check 捕获：百分比超过 100% 且 denominator 是 count rows，应回查 amount 字段和 row grain。

## 类别三：值、操作符、格式语义错误

这类严格保留的 case 都能通过当前 DB 的值域或 SQLite 行为验证。

### `european_football_2/q1060/q1118`

q1060 中 `born after 1990` 应是年份大于 1990，不是等于 1990。q1118 中年龄计算不能用 SQLite 对 datetime 字符串直接相减，因为那只是减年份前缀；应使用 `JULIANDAY` 计算年龄。

这类问题需要把 operator/value semantics 作为 profile：

```text
operator phrase:
  after year X -> strftime('%Y', date) > X
age >= N -> (julianday('now') - julianday(birthday)) / 365 >= N
```

### `formula_1/q860/q892/q897/q974/q975/q986/q1012`

这些题集中在 Formula 1 时间字段：

- `lapTimes.time` 是展示字符串。
- `lapTimes.milliseconds` 是机器可比较数值。
- `qualifying.q1/q2/q3` 是排位赛分段时间。
- `results.fastestLapTime` 是 fastest lap 展示字符串。

Pontis 需要的不是更长 column brief，而是 canonical time profile：字段单位、格式、可比较性、何时用字符串匹配、何时用 milliseconds 排序。

### `card_games/q398/q446/q486/q511/q514`

这些是卡牌库里的值/公式/枚举行为：mana cost、converted mana cost、mode、percentage denominator 等。数据库中通常能通过值域、列名和样例排除错误选择。比如 `manaCost` 和 `convertedManaCost` 一个是符号字符串，一个是数值转换结果，不能互换。

## 类别四：join path / coverage / date ownership 错误

严格口径下，join 类问题不再包含 SAT-FRPM 补零和 toxicology orphan molecule 这种 benchmark 契约问题。保留的 join 问题必须是数据库本身能明确指出当前 join path 错了。

### `california_schools/q43/q51`

这两题的问题不是 q10 那种补零契约，而是 LEFT JOIN/未匹配行导致输出 NULL 或选择到缺失实体。题目要求 county 或 mailing address 时，当前 SQL 把无法匹配的 school 留在候选中，导致输出空值。数据库探索应检查 top candidate 是否有可用目标属性；如果没有，应比较 inner join 或可匹配候选。

### `thrombosis_prediction/q1187/q1269`

Pontis 把 date filter 放到 `Examination."Examination Date"`，但目标指标来自 `Laboratory`，正确日期应绑定到 `Laboratory.Date`。这是 measurement date ownership 问题：同一个 patient 可以同时出现在多个 measurement table，每个指标应使用自己表里的时间戳。

需要的 profile：

```text
measurement table:
  Laboratory: lab metrics + Date
  Examination: examination diagnosis/status + Examination Date
rule: filters on lab metric thresholds should prefer Laboratory.Date unless query explicitly asks exam table fields
```

### `financial/q169/q182`

q169 中 `disp` 同一 account 可有 `OWNER` 和 `DISPONENT`，不加 `disp.type='OWNER'` 会重复计入贷款金额。q182 中 “payment” 应落到 transaction records，而不是 standing order。数据库里 `trans` 和 `order` 都有 `SIPO`，但 `trans` 才是实际 payment event。

这类需要 join path profiler 记录 join 后 row count 变化，以及 business-event table 和 configuration/order table 的差别。

### `toxicology/q330`

问题要求 triple bonded hydrogen atoms。直接用 atom 与 connected 的双向边去找 hydrogen triple bond 会得到空或错误集合；golden 语义用 molecule-level triple bond 与 atom element 联合过滤。这里需要比较 `bond -> molecule -> atom` 与 `connected -> atom` 两条路径的 coverage 和输出差异。

## 归纳出来的 Pontis 短板

严格 68 个 case 显示，Pontis 的主要问题不是没有图谱，而是图谱没有被组织成可决策的 profile。

### A. 缺少候选列/候选表竞争

Pontis 常能找到相关表，但不会系统比较多个候选落点：

- `cards.text` vs `rulings.text`
- `foreign_data.language` vs `set_translations.language`
- `event.type` vs `budget.category`
- `races.url` vs `circuits.url`
- `Patient.Description` vs `Patient."First Date"`

建议新增 `predicate_column_competition`。

### B. row grain 没有成为一等实体

需要为表和 join path 生成结构化 row grain：

- `yearmonth`: customer-month
- `Player_Attributes`: player-date snapshot
- `hero_power`: hero-power edge，但可按 name-level 聚合
- `budget`: event-budget-category line

建议新增 `row_grain_analyzer`。

### C. value profile 不够可执行

仅有 top values 不够。需要能回答：

- 目标 value 是否真实存在。
- `LIKE`、`=`、大小写是否影响结果。
- 时间字段是字符串还是数值毫秒。
- 阈值比较应该用 raw text、cast，还是专门 parser。

建议新增 `value_semantics_profiler`。

### D. join path 缺少 coverage 和 ownership

需要记录：

- join 后 row count 是否放大或丢失。
- left-only/right-only 示例。
- 指标字段和日期字段是否属于同一 measurement table。
- entity table、event table、transaction table、configuration table 的角色差异。

建议新增 `join_path_profiler`。

## 68 个 case 对 explorer 的具体改造映射

| 改造项 | 直接覆盖的主要类别 | 优先覆盖的严格 case |
|---|---|---|
| `predicate_column_competition` | 表/列/谓词落点 | `card_games/q408/q433/q465`, `student_club/q1418/q1419/q1422`, `formula_1/q849/q851/q922`, `thrombosis_prediction/q1186/q1233/q1264/q1271` |
| `row_grain_analyzer` | row grain / 聚合口径 | `debit_card_specializing/q1498`, `european_football_2/q1037/q1093/q1148`, `superhero/q720/q741`, `student_club/q1360` |
| `value_semantics_profiler` | 值、操作符、格式语义 | `european_football_2/q1060/q1118`, `formula_1/q860/q892/q897/q974/q975/q986/q1012`, `card_games/q398/q446/q486/q511/q514` |
| `join_path_profiler` | join path / coverage / ownership | `california_schools/q43/q51`, `thrombosis_prediction/q1187/q1269`, `financial/q169/q182`, `toxicology/q330` |

## 更具体的实现建议

### 1. 图谱里增加结构化 Profile 节点

建议新增：

- `PredicateLandingProfile`
- `RowGrainProfile`
- `ValueBehaviorProfile`
- `JoinCoverageProfile`
- `MeasurementOwnershipProfile`

示例：

```json
{
  "type": "RowGrainProfile",
  "table": "yearmonth",
  "row_grain": "customer_month",
  "entity_key": ["CustomerID", "Date"],
  "aggregation_warning": "Date alone has multiple rows; monthly total requires GROUP BY Date SUM(Consumption)"
}
```

### 2. query-time planner 必须展示候选策略对比

当前 Pontis 经常查了很多信息，但最终仍凭直觉选。应让 planner 在 final SQL 前展示少量候选策略：

```text
candidate strategies:
  A event.type -> values: Meeting/Election/Guest Speaker
  B budget.category -> values: Food/Parking/Advertisement/Speaker Gifts
question asks: category of events
choose: B, because literal category column exists and is linked to event
```

这个模式比单纯把更多 metadata 塞进上下文更有效。

### 3. final SQL 前做 DB-only sanity checks

只做数据库内生检查，不写 BIRD 专用规则：

- 输出行列数是否符合问题。
- 候选列命中值是否存在。
- join 前后 row count 是否放大或丢失。
- row grain 选择是否和 question/evidence 的聚合对象一致。
- 使用 cast/round/distinct/limit 是否改变结果。

### 4. 对“看似合理但未被问题要求”的清洗降权

严格 112 里仍有一些边界 case 提醒我们：模型很容易因为“更聪明”而做清洗或常识修正。最终策略应是：如果清洗会改变结果，必须展示改变幅度，并让 agent 明确说明 question/evidence 是否要求它。

## 更新后的优先级

基于严格 68 个 Pontis 仍错 case，优先级应是：

1. `predicate_column_competition`
   - 覆盖最大，尤其 `card_games`、`student_club`、`formula_1`、`thrombosis_prediction`。
2. `row_grain_analyzer`
   - 解决 Pontis 在 customer-month、player snapshot、hero name 聚合、budget line 上的系统错误。
3. `value_semantics_profiler`
   - 对 `formula_1` 时间字段、`card_games` mana/mode、`european_football_2` 日期年龄尤其关键。
4. `join_path_profiler` / `MeasurementOwnershipProfile`
   - 数量较少但高价值，尤其医学库日期归属、financial owner/payment、california nullable join。

## 对 Pontis 的核心评价

严格复审后，Pontis 的图谱路线仍然有效：在 112 个真正 DB-exploration-fixable case 中，Pontis 已修掉 44 个，修复率 39.3%。但剩下 68 个说明当前图谱仍偏“描述型 metadata”，不够“决策型 profile”。

下一步不应该继续把所有错误都归到 readme 或 golden style，也不应该用 q10 补零这种 benchmark 契约题来惩罚 explorer。真正值得优化的是：让图谱能系统回答“这个语义短语应该落到哪个表/列、当前表是什么 row grain、这个值/时间/单位如何比较、这条 join path 会不会改变结果”。
