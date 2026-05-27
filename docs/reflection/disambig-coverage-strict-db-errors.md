# Pontis 消歧实体覆盖严格 DB 错例调研

本文按 `pontis-db-exploration-fixable-analysis.md` 当前的严格口径分析：严格 DB_EXPLORATION_FIXABLE 为 112 题，其中 Pontis 仍错 68 题。用户提到的 122 若指另一个集合，需要用对应 qid 列表重跑；本次先以现有严格 112/68 清单为准。

## 结论

Pontis 现在的消歧实体没有稳定起到“附着在基础实体上、读实体时自动跳出”的作用。

在 68 个 Pontis 仍错的严格 DB 探索错例里，运行日志中出现过 `disambig` 或“消歧”痕迹的只有 16 个。真正属于“相关消歧信息已经被读到，但模型最后仍没遵守”的 case 很少，主要是 `california_schools/q43` 和 `formula_1/q861`。更多 case 是下面三种：

1. 相关消歧实体已经存在，但 query 阶段没有自然暴露出来。
2. query 阶段读到了某些消歧实体，但这些实体和当前错误点不相关，或者覆盖粒度不够。
3. 当前错误不是传统“同名/近义列消歧”能解决的，需要把 row grain、join ownership、predicate role、aggregation grain 等信息也作为基础实体的常驻 profile 展示。

所以，当前瓶颈主要不是“模型看到了消歧但顽固不听”，而是“消歧实体没有以正确的触发条件和正确粒度进入 agent 的工作上下文”。

## 机制问题

`Pontis/tool/config.py` 里 `col` 的 meta 会展示相邻 `disambig`：

```python
adjacency_keys={"fk", "rel", "disambig", "table"}
```

但 `table` 的 meta 虽然 `default_keys` 写了 `disambig`，实际邻接展示没有包含 `disambig`：

```python
adjacency_keys={"col", "fk", "rel"}
```

这会导致 agent 只读表、路径、query 结果时，很容易完全看不到表级或跨列消歧。

另外 `SQLDisambigCheck` 当前主要根据最终 SQL 里的表名和 disambig 相邻表匹配。如果消歧实体只连到了列，或者实体名如 `language_disambiguation` 不会出现在 SQL 中，它就很难触发。并且它只要求“展示过”即可，不强制读 meta，也不检查最终 SQL 是否遵守。

## 已有相关消歧/标注，但没有遵守

### `california_schools/q43`

问题是最低 SAT 总分学校的 math score 和 county。Pontis 读到了：

- `county_city_district_choice:disambig`

但最终仍输出：

```sql
SELECT s.AvgScrMath, s.cname
FROM satscores s ...
```

gold 使用 `satscores.cds = schools.CDSCode` 后输出 `schools.County`。

这里消歧实体有用，但不够强。它提示 County/City/District 的候选来源，却没有把“输出 county 时优先使用 canonical school location table，而不是 SAT 聚合表的 cname”变成足够硬的选择规则。模型选择了更短路径。

### `formula_1/q861`

问题是 qualifying race 中某个 q3 时间对应 driver 的 number。Pontis 读到了：

- `driver_number:disambig`
- `qualifying.number` 的 detail：排位赛中车手的赛车号码，与 `drivers.number` 不同
- `drivers.number` 的 detail：车手职业生涯固定参赛号码

但最终仍输出：

```sql
SELECT q.number
FROM qualifying q ...
```

gold 输出 `drivers.number`。

这是最典型的“标注已经出现但最终决策没遵守”。不过实体本身也有缺口：`driver_number:disambig` 明确覆盖了 `results.number` vs `drivers.number`，但没有把 `qualifying.number` 也纳入同一个竞争簇。因此模型虽然看到了提示，仍把当前表的 `number` 当成合法候选。

### `financial/q169`

这不是 disambig 失败，而是基础实体 detail 没有被遵守。`disp` 表 detail 已经写明只有 `OWNER` 类型客户有贷款/订单资格，但最终 SQL 没有限制 `disp.type='OWNER'`。这说明部分错误需要“关键实体 profile”被最终 SQL 审查，而不是只靠 disambig。

## 相关消歧存在，但 query 阶段没有读到

### `card_games/q433/q465`

这两题涉及中文/韩文版本和 set/card translation 的选择。库里有 `language_disambiguation`，但 q433/q465 的日志没有读到有效消歧实体。模型分别在 `foreign_data` 和 `set_translations` 之间摇摆。

这类问题适合让语言、翻译、localized name/type 相关 profile 自动附着到 `foreign_data`、`set_translations`、`cards`、`sets` 的 table/column meta 上，而不是等待 agent 主动 `find("*:disambig", "language")`。

### `thrombosis_prediction/q1186/q1233`

extract 阶段已经创建/展示过：

- `BEHCET_vs_Behcet:disambig`
- `Diagnosis_casing`
- `Description_date_misnaming`

但 q1186/q1233 的 query 日志没有读取这些 disambig。q1186 在 `Patient.Diagnosis/Description` 和 `Examination.Diagnosis/Examination Date` 之间反复探索，最后仍走错落点；q1233 也涉及 `First Date` vs `Description`。

这说明“已有 disambig”没有自动挂到 `Patient`、`Examination`、相关 date/diagnosis 列的可见上下文中。

### `financial/q182`

错误在 transaction/order/payment semantics 之间。库里存在金融字段相关 disambig，但 query 日志没有看到对关键 payment/order choice 的有效消歧读取。这个 case 即使用 disambig，也需要更明确的 table-role profile：`trans` 是实际交易流水，`order` 是常设支付指令，不是已发生交易。

## 读到了消歧，但消歧不相关或覆盖不足

### `card_games/q438`

读到了 `language_disambiguation`，但错误主要是 ID 映射：`sets.id`、`sets.code`、`set_translations.setCode`、`foreign_data.uuid` 的连接角色。语言消歧不能解决 join-key 选择。

### `card_games/q444/q448/q482`

读到了 language/type 相关消歧，但错误点是 `cards.name/type` vs `foreign_data.name/type` 的输出语义。现有 `type_column_disambiguation` 主要覆盖 `cards.type`、`types`、`originalType`、`subtypes/supertypes`，没有把 `foreign_data.type` 纳入“本地化 type 文本 vs canonical card type”的竞争簇。

### `card_games/q514`

读到了 language/type 消歧，但问题是 `manaCost` vs `convertedManaCost`。这是一个公式/字段角色选择问题，现有消歧不覆盖。

### `debit_card_specializing/q1504`

读到了 `date_format` 和 `monetary_amount`，但模型选择 `yearmonth.Consumption`。对于 gold 来说，应使用 `transactions_1k.Amount`。这里现有“monthly/yearly query 用 yearmonth”一类提示反而可能把模型推向非 gold 路径。这个 case 更像 dataset prior / benchmark contract 边界，不应简单算作消歧可解。

### `codebase_community/q652`

读到了 `display_name_duplicates`、`creationdate_ambiguity` 等，但错误是 `posts.CreaionDate`/`postHistory` 选择以及字段拼写。现有消歧没有覆盖“编辑历史/帖子创建时间/用户 badge 时间”的事件源角色。

### `student_club/q1419`

读到了 event status、budget amount、date 相关消歧，但当前错点是 event type/category/budget category 的谓词落点。需要 category role profile，不是已有消歧可解决。

## 基本没有相关消歧的类型

以下大量 case 当前不是“已有消歧没遵守”，而是缺少能解决错误的常驻实体属性：

- row grain / aggregation grain：`superhero/q720/q741`、`debit_card_specializing/q1498`、部分 `european_football_2`。
- join ownership / event source：`financial/q169/q182`、`thrombosis_prediction/q1187/q1269`、`toxicology/q330`。
- predicate column competition：`student_club/q1418/q1422`、`formula_1/q849/q851/q922`、`thrombosis_prediction/q1264/q1271`。
- formula/metric choice：`card_games/q514`、`formula_1/q860/q892/q897/q974/q975/q986/q1012`。
- molecular graph semantics：`toxicology/q215/q217/q257/q281/q330`，需要 atom/bond/connected 表的行粒度和方向语义 profile。

这些信息可以统一写入基础实体的一个属性，而不是每类错误新建一个属性。

## 建议：把消歧升级成自动展示的 Entity Guidance

不要依赖 agent 主动找 `*:disambig`。建议在每个 table/column 的 meta 输出中自动显示一个统一属性，比如：

```text
guidance:
- role: canonical school location; use schools.County for county output when joining from SAT rows by CDSCode.
- contrast: satscores.cname is a SAT-reporting name field, not the canonical county column.
- grain: one row per SAT score record; join to schools for stable school attributes.
```

这个属性可以由 explorer 写入，也可以从 disambig/rel/knowledge 聚合生成。关键是它挂在基础实体上，agent 读 `table` 或 `col` 时自动看到。

建议统一 schema：

```json
{
  "guidance": [
    {
      "trigger": "county / city / district output",
      "choose": "schools.County",
      "avoid": "satscores.cname for county output",
      "reason": "schools is the canonical school-location table; satscores is a score fact table"
    },
    {
      "trigger": "driver number as driver attribute",
      "choose": "drivers.number",
      "avoid": "qualifying.number/results.number unless asking race-entry car number",
      "reason": "qualifying/results number is event-specific car number"
    }
  ]
}
```

这样仍然只有一个属性，不需要为每种错题增加专用字段。展示层可以把 `guidance` 压缩成 3-6 条高优先级提示，避免 meta 爆炸。

## 优先修改点

1. `table` meta 的邻接展示加入 `disambig`，否则表级消歧不会自然出现。
2. 把 disambig/knowledge/rel 中的关键选择规则聚合进基础实体的 `guidance` 属性。
3. 让 `guidance` 支持 `trigger / choose / avoid / reason`，统一承载消歧、行粒度、join ownership、metric role。
4. 改 `SQLDisambigCheck` 或最终 SQL 审查逻辑：不要只检查“是否展示过 disambig”，而要在候选 SQL 涉及 `guidance.avoid` 或未使用 `guidance.choose` 时触发二次确认。
5. 对 explorer 增加一个专门步骤：扫描同名列、近义列、fact/dimension 表、日期列、金额列、ID/code 列、翻译表、桥表，生成基础实体 guidance。

简短判断：当前 68 个 Pontis 仍错的严格 DB 探索错例中，只有少数是“有明确消歧但没遵守”；更多是“消歧没有自动进入上下文”或“现有消歧粒度不覆盖真正错误点”。因此最有效的方向是把消歧实体从独立节点转化为基础实体自动展示的 `guidance`，而不是继续要求 agent 主动搜索消歧节点。
