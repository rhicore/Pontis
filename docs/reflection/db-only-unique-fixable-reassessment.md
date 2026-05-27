# DB-only 唯一可解错题复审

本文复审 `DB_EXPLORATION_FIXABLE` 这个标签是否真的成立。核心问题是：

> 在不知道 golden SQL、也不看其他 query-SQL pair 的情况下，只给一个足够强的模型当前 question/evidence/schema/完整数据库内容，它是否能唯一推出接近 golden 的 SQL？

结论：不能把此前所有 `DB_EXPLORATION_FIXABLE` 都当作图谱建设上限。相当一部分题在数据库内存在多个合理解释，或者 golden 依赖数据集契约、evidence 习惯、输出形状，而不是数据库事实本身。

## 数字口径

我从 Bash reflection run 重建得到 `145` 个被模型标为 `DB_EXPLORATION_FIXABLE` 的错题：

`workspace/baselines/bash_agent/runtime_logs/bird_dev_20260524_031500_bash_agent_bird_dev_full_reflection_w50_fixed`

现有文档 `Pontis/docs/pontis-db-exploration-fixable-analysis.md` 曾把这个集合收紧为约 `112` 个严格 case。你提到的 `122` 在当前文档和日志里没有一个稳定的显式列表；它更像是中间人工口径。下面不纠结旧数字，而是给出新的严格判据和高置信列表。

## 严格判据

一个 case 只有满足下面三条，才算真正 `DB_ONLY_UNIQUE_FIXABLE`：

1. 数据库中存在决定性证据，能排除错误 SQL 的表、列、值、join path、row grain 或聚合口径。
2. 修正不依赖 golden SQL 的隐含风格、其他 query-SQL pair、BIRD 特有表达习惯。
3. 若存在两个业务上同样合理的 SQL，只是 golden 选了其中一个，则不算 DB-only 唯一。

按这个标准，高置信 DB-only 唯一可解的题大约只有 `67/145`。如果只看旧文档的 112/122 工作集，也应进一步下调，而不是继续假设都能靠 explorer 解决。

## 高置信 DB-only 唯一可解列表

这些题才适合用来衡量图谱/explorer 的真实上限。

| DB | qid |
|---|---|
| `california_schools` | q4, q20, q74 |
| `card_games` | q382, q384, q403, q407, q410, q438, q446, q449, q453, q480, q486, q511 |
| `codebase_community` | q569, q576, q635, q652, q662, q672 |
| `debit_card_specializing` | q1470, q1479, q1502, q1504 |
| `european_football_2` | q1118, q1148 |
| `financial` | q124, q169 |
| `formula_1` | q851, q860, q861, q871, q881, q894, q897, q949, q964, q975, q1017 |
| `student_club` | q1360, q1387, q1418, q1419, q1422, q1434, q1447 |
| `superhero` | q720, q741, q803 |
| `thrombosis_prediction` | q1187, q1202, q1233, q1245, q1264, q1275, q1276, q1296, q1309 |
| `toxicology` | q215, q217, q224, q257, q275, q281, q321, q334 |

## 为什么很多题要降级

### 1. join coverage / unmatched row 不等于唯一答案

例子：`california_schools/q10/q19/q42/q43/q51`

这些题常见差异是：预测 SQL 保留 SAT top row，但该 row 无法 join 到 `schools/frpm`；golden 用 inner join 后在可匹配行里排序。数据库能告诉我们存在 unmatched row，但不能唯一告诉我们 benchmark 是要返回 `NULL`、跳过 unmatched row，还是按其他方式补齐。这里有明显 benchmark 契约成分。

因此这类题不应再当作 explorer 高置信上限。

### 2. 精确列名存在，但业务解释仍有多解

例子：`california_schools/q80`

问题问最高 latitude 学校的 school type。`frpm` 有精确列名 `School Type`，但 `schools` 也有 `SOCType/EILName` 等学校类型字段。一个只看数据库的强模型很可能选择 `schools.SOCType`，因为学校实体和 latitude 都在 `schools`。golden 选择 `frpm.School Type`，更像数据集偏好，不是 DB-only 唯一。

### 3. 同名概念跨表存在，数据库不能决定题面指哪一个

例子：

- `financial/q182`: household payment 可落到 `order.k_symbol='SIPO'`，也可落到 `trans.k_symbol='SIPO'`。数据库能显示两者都存在；golden 选 `trans`，不是唯一事实。
- `card_games/q433`: Chinese Simplified 可指 `foreign_data.language` 的 card translation，也可指 `set_translations.language` 的 set translation。题面 "set of cards" 不足以唯一排除 card-level interpretation。
- `thrombosis_prediction/q1186`: exam date 可自然落到 `Examination.Examination Date`，但 evidence 又指向 `Patient.Description`。这是输入信息冲突，不是单纯探索不足。

### 4. row grain / DISTINCT / 输出形状经常是 benchmark 风格

例子：

- `thrombosis_prediction/q1190/q1223/q1241/q1267/q1277`: 题面说 patients，预测用 distinct patient 很自然；golden 经常按 Laboratory row 计数。
- `toxicology/q298/q317`: 问 molecule percentage，预测按 distinct molecule 很自然；golden 按 atom row 计。
- `card_games/q408`: 问 "How many"，golden 返回 `rulings.text`。这是输出形状冲突，不应归因于纯数据库探索。

### 5. 更干净或更精确的 SQL 反而不等于 golden

例子：

- `formula_1/q1011`: 数据库有完整的 `lapTimes.milliseconds`，这是比解析 `time` 文本更自然、更稳定的字段；golden 选择解析 `time`。
- `toxicology/q225/q332`: 是否把 `bond` 表里存在、但 `molecule` 表缺失的 molecule_id 计入，库内并不一致。
- `card_games/q393`: 预测额外使用 `hasFoil=1` 从自然语言看更严格，golden 没用。

这些不是 explorer 缺失，而是 golden/evidence 契约问题。

## 高置信可解 case 的共同特征

真正 DB-only 唯一的 case 通常有明确数据库反证：

- 候选列里有一个列名和值域直接匹配题面，另一个不匹配。
- 错误 SQL 的值过滤返回空集，正确值在 topk/sample 中明确存在。
- 错误 join path 明确丢行或引入重复，数据库能用 row count / coverage 证明。
- 表粒度能直接解释题面：实体表 vs 交易表、月度汇总表 vs 明细表、历史属性表 vs 主表。
- evidence 里的公式与数据库 row grain 一致，且没有与自然语言冲突。

这类 case 是后续 explorer 应优先优化的对象。

## 对 Pontis 图谱优化的含义

图谱建设不应该以 145/122/112 作为上限，而应先用 `DB_ONLY_UNIQUE_FIXABLE` 子集评估。否则会把很多本来需要 dataset prior 或微调的 case 误算成 explorer 失败。

优先做四类 profile：

1. `candidate_column_competition`: 同义/近义列的候选比较，附带值域和表语义。
2. `row_grain_profile`: 每张表的记录粒度、是否一实体多行、历史快照还是事件明细。
3. `join_coverage_profile`: 候选 join path 的覆盖率、是否会丢 top candidate、是否会重复计数。
4. `value_grounding_profile`: 题面值、evidence 值、真实枚举值之间的映射和空集反证。

但对于 `DATASET_PRIOR_REQUIRED`，图谱只能暴露多个合理解释，不能保证唯一对齐 golden。那些需要 query history、few-shot、prompt 约束或微调解决。

