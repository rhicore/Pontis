# Pontis vs DeepEye BIRD Dev Business 对比

## 对比对象

- Pontis:
  - run: `20260630_063613_bird_dev`
  - result: `workspace/baselines/pontis/results/20260630_063613_bird_dev/pontis_agent/summary.md`
  - eval mode: `business`
- DeepEye:
  - run: `20260605_deepeye_qwen24k_shards_ab_business`
  - result: `workspace/baselines/deepeye_sql/evaluation/20260605_deepeye_qwen24k_shards_ab_business/summary.md`
  - eval mode: `business`

## 总体结果

| System | Business | Strict |
|---|---:|---:|
| Pontis | 1040/1534 (67.80%) | 911/1534 (59.39%) |
| DeepEye | 1132/1534 (73.79%) | 1088/1534 (70.93%) |
| Pontis - DeepEye | -92 (-6.00 pp) | -177 (-11.54 pp) |

## 分库 Business 对比

| Database | Pontis | DeepEye | Delta |
|---|---:|---:|---:|
| california_schools | 61/89 (68.54%) | 62/89 (69.66%) | -1 (-1.12 pp) |
| card_games | 107/191 (56.02%) | 128/191 (67.02%) | -21 (-10.99 pp) |
| codebase_community | 127/186 (68.28%) | 137/186 (73.66%) | -10 (-5.38 pp) |
| debit_card_specializing | 43/64 (67.19%) | 45/64 (70.31%) | -2 (-3.12 pp) |
| european_football_2 | 96/129 (74.42%) | 100/129 (77.52%) | -4 (-3.10 pp) |
| financial | 69/106 (65.09%) | 80/106 (75.47%) | -11 (-10.38 pp) |
| formula_1 | 108/174 (62.07%) | 120/174 (68.97%) | -12 (-6.90 pp) |
| student_club | 137/158 (86.71%) | 138/158 (87.34%) | -1 (-0.63 pp) |
| superhero | 115/129 (89.15%) | 116/129 (89.92%) | -1 (-0.78 pp) |
| thrombosis_prediction | 84/163 (51.53%) | 107/163 (65.64%) | -23 (-14.11 pp) |
| toxicology | 93/145 (64.14%) | 99/145 (68.28%) | -6 (-4.14 pp) |

## 分库 Strict 对比

| Database | Pontis | DeepEye | Delta |
|---|---:|---:|---:|
| california_schools | 46/89 (51.69%) | 59/89 (66.29%) | -13 (-14.61 pp) |
| card_games | 92/191 (48.17%) | 124/191 (64.92%) | -32 (-16.75 pp) |
| codebase_community | 121/186 (65.05%) | 134/186 (72.04%) | -13 (-6.99 pp) |
| debit_card_specializing | 39/64 (60.94%) | 42/64 (65.62%) | -3 (-4.69 pp) |
| european_football_2 | 84/129 (65.12%) | 96/129 (74.42%) | -12 (-9.30 pp) |
| financial | 58/106 (54.72%) | 79/106 (74.53%) | -21 (-19.81 pp) |
| formula_1 | 91/174 (52.30%) | 115/174 (66.09%) | -24 (-13.79 pp) |
| student_club | 125/158 (79.11%) | 134/158 (84.81%) | -9 (-5.70 pp) |
| superhero | 108/129 (83.72%) | 114/129 (88.37%) | -6 (-4.65 pp) |
| thrombosis_prediction | 73/163 (44.79%) | 103/163 (63.19%) | -30 (-18.40 pp) |
| toxicology | 74/145 (51.03%) | 88/145 (60.69%) | -14 (-9.66 pp) |

## 主要差距

- Business 差距最大：
  - `thrombosis_prediction`: -23
  - `card_games`: -21
  - `formula_1`: -12
  - `financial`: -11
  - `codebase_community`: -10
- Business 最接近：
  - `california_schools`: -1
  - `student_club`: -1
  - `superhero`: -1
  - `debit_card_specializing`: -2

## 备注

- 本文只记录同一套 BIRD dev 结果在 `business` 评测口径下的分库差异。
- Pontis 本轮 relaxed match types:
  - exact: 911
  - column_reorder: 11
  - predicted_superset: 88
  - tie_superset: 20
  - value_equivalent: 10
  - execution_error: 45
  - no_match: 449
- DeepEye 对照 relaxed match types:
  - exact: 1088
  - column_reorder: 2
  - predicted_superset: 33
  - tie_superset: 9
  - no_match: 402

## Gap 题定义

下面分析的 gap 题指：

- DeepEye `business_correct = true`
- Pontis `business_correct = false`

这不是净差值。比如某库 DeepEye 对 Pontis 错 36 题，同时 Pontis 对 DeepEye 错 13 题，则净差是 -23，但 gap 来源题是 36 题。

全库共有 173 道 gap 题。

| Database | Gap 题数 |
|---|---:|
| california_schools | 11 |
| card_games | 25 |
| codebase_community | 17 |
| debit_card_specializing | 7 |
| european_football_2 | 11 |
| financial | 15 |
| formula_1 | 20 |
| student_club | 6 |
| superhero | 5 |
| thrombosis_prediction | 36 |
| toxicology | 20 |

## Gap 主因二分

人工按主因强制二分：

- **BIRD 风格 / golden 口径问题**：Pontis 选择了业务上更自然或更稳健的 SQL 形态，但和 BIRD gold 的行粒度、输出列、原始字段、精确条件或排序截断口径不一致。
- **Meta / schema linking 问题**：Pontis 选错了表、字段、关系路径、实体口径或值口径。

| 类别 | 数量 | 占比 |
|---|---:|---:|
| BIRD 风格 / golden 口径问题 | 136 | 78.6% |
| Meta / schema linking 问题 | 37 | 21.4% |

分库主因：

| Database | Gap 题数 | BIRD 风格 | Meta/schema linking |
|---|---:|---:|---:|
| california_schools | 11 | 6 | 5 |
| card_games | 25 | 23 | 2 |
| codebase_community | 17 | 15 | 2 |
| debit_card_specializing | 7 | 4 | 3 |
| european_football_2 | 11 | 11 | 0 |
| financial | 15 | 6 | 9 |
| formula_1 | 20 | 14 | 6 |
| student_club | 6 | 4 | 2 |
| superhero | 5 | 5 | 0 |
| thrombosis_prediction | 36 | 35 | 1 |
| toxicology | 20 | 13 | 7 |

结论：Pontis 与 DeepEye 的差距主体不是知识图谱缺字段，而是生成 SQL 时没有贴住 BIRD gold 的行粒度和原始字段口径。`financial` 和 `toxicology` 的 schema/linking 占比相对更高。

## 前 150 道 Pontis business wrong 人工分类

这一节从前 150 道人工分类续写到全量 494 道 `business_correct = false`，不使用 DeepSeek。分类是人工逐题读 question、evidence、gold SQL、Pontis SQL 后给出的主因；每题只放入一个主类，概要里会标出明显次因。

### 输出列 / 输出形状 / 标签格式

- `california_schools` Q1：题目只要最低三个 rate，Pontis 额外输出 school name；同时用了分母过滤替代 gold 的表达式非空过滤。
- `california_schools` Q23：SQL 语义接近，但输出别名/形状与 gold 的最小 `School, Street` 结果不完全一致，需复核业务评测未放宽原因。
- `california_schools` Q33：gold 输出 `Website, School Name` 且过滤 website 非空；Pontis 输出 `School, Website`，列顺序和非空条件都不同。
- `california_schools` Q36：题面说最多 3 个管理员，gold 输出三组姓名；Pontis 只输出前两组。
- `california_schools` Q49：gold 要 `County, School, ClosedDate`，Pontis 少了 county。
- `california_schools` Q57：gold 只要 phone/ext，Pontis 额外输出 satscores.cds。
- `california_schools` Q88：gold 输出 `AdmEmail1, School`，Pontis 输出 `school_name, admin_email` 且额外限制 `rtype='S'`。
- `card_games` Q340：题面/gold 要 card id，Pontis 返回 name。
- `card_games` Q341：gold 返回 id，Pontis 返回 name；同时“without powerful foils”的 OR 条件也比 gold 更合理但不一致。
- `card_games` Q343：gold 返回 id，Pontis 返回 name。
- `card_games` Q344：gold 返回 distinct id，Pontis 返回 name 和 setCode。
- `card_games` Q386：gold 是 count，Pontis 返回每张卡的 name/format/status 明细。
- `card_games` Q389：gold 返回 id/date，Pontis 返回 name/date。
- `card_games` Q402：gold 只返回一个 percentage，Pontis 同时返回 percentage 和每个 id。
- `card_games` Q429：gold 返回 language/type，Pontis 额外返回 set id/code/name/translation。
- `card_games` Q430：gold 要 set name 和 id，Pontis 只返回 set code。
- `card_games` Q432：gold 返回 set id，Pontis 返回 set name。
- `card_games` Q437：gold 返回 card id，Pontis 返回 name。
- `card_games` Q442：gold 输出 `baseSetSize, setCode`，Pontis 输出 `code, baseSetSize`。
- `card_games` Q448：gold 返回 card name/type，Pontis 返回 foreign name/language/type，输出对象变了。
- `card_games` Q459：gold 只返回 converted mana 更高的 name 且 `LIMIT 1`，Pontis 多返回 convertedManaCost 且没有截断到 1。
- `card_games` Q469：gold 返回 `YES/NO`，Pontis 返回 set code/name 和 0/1 标记。
- `card_games` Q473：gold 返回 `YES/NO`，Pontis 返回聚合后的 0/1 boolean。
- `card_games` Q483：gold 要 Italian text，Pontis 额外输出 Italian card name 和 ruling text。
- `card_games` Q494：gold 要 ruling text 和 `YES/NO`，Pontis 额外输出 card name、raw boolean 和解释列。
- `card_games` Q514：gold 只要 top 10 card name，Pontis 额外输出 manaCost/convertedManaCost，并按 convertedManaCost 排。
- `card_games` Q530：gold 按 legality 行返回 frameVersion、card name 和 banned-name/NO，Pontis 聚合成每张卡一个 Yes/No 标记。
- `codebase_community` Q565：gold 标签是 `well-finished`/`NOT well-finished`，Pontis 返回 `yes`/`no`。
- `codebase_community` Q586：gold 返回 user display name 和 post title，Pontis 少了 title。
- `codebase_community` Q596：gold 返回一个 badge name，Pontis 返回 display name、badge date 等额外列，并扩大为多行 badge 明细。
- `codebase_community` Q599：gold 每个 postHistoryTypeId 一行并带唯一评论用户数，Pontis 把 type id 聚合成一个字符串。
- `codebase_community` Q628：gold 返回 `Id, DisplayName`，Pontis 返回 `DisplayName, Views`。
- `codebase_community` Q649：gold 返回 postHistory id 与 post 的 LastEditDate 明细，Pontis 返回聚合 count 和 max edit date。
- `codebase_community` Q679：gold 输出 users.Id 和 title，Pontis 输出 posts.Id 和 title，ID 口径不同。
- `codebase_community` Q686：gold 要列出 view count 高于平均值的 post Id，Pontis 改成 count。
- `codebase_community` Q693：gold 只返回 post count，Pontis 返回 post_count 和 comment_count 两列。
- `codebase_community` Q711：gold 计 comments.id，Pontis 计 distinct UserId，输出指标对象不同。
- `debit_card_specializing` Q1480：gold 只输出峰值月份，Pontis 输出完整年月和 total_consumption。
- `debit_card_specializing` Q1491：gold 输出 top country 和一个总数，Pontis 输出所有 country 的分组计数。
- `debit_card_specializing` Q1503：gold 输出 ProductID 和 Description，Pontis 少了 ProductID。
- `debit_card_specializing` Q1512：gold 只输出 CustomerID，Pontis 额外输出 Segment、Currency 和 total_paid。
- `debit_card_specializing` Q1520：gold 输出 CustomerID、Date、Consumption，Pontis 少了 CustomerID。
- `debit_card_specializing` Q1527：gold 只输出 GasStationID，Pontis 额外输出 total_revenue 且排序公式不同。
- `european_football_2` Q1024：gold 输出 Player_Attributes.id，Pontis 输出 player_api_id 和 max_crossing。
- `european_football_2` Q1058：gold 输出 `Max`/`Min` 标签，Pontis 输出 player_name、height、avg_finishing 明细。
- `european_football_2` Q1064：gold 输出 id 和 player_name，Pontis 只输出 player_name。
- `european_football_2` Q1144：gold 输出 attribute id、finishing、curve，Pontis 输出 player_name、finishing、curve。
- `financial` Q124：gold 要 district A2，Pontis 输出 region A3。
- `financial` Q129：gold 只输出 district name，Pontis 额外输出 total_withdrawal 并按总额排序。
- `financial` Q133：gold 输出 district_id 和 A2，Pontis 输出 A2 两次且保留并列最高。
- `financial` Q172：gold 分别输出 OWNER 和 DISPONENT 两个计数，Pontis 只输出总 count。
- `financial` Q180：gold 输出 client_id 和 account_id，Pontis 输出 client_id 和 birth_date。
- `financial` Q193：gold 输出 client_id、district_id、A2，Pontis 少 district_id 且 district 来源不同。
- `formula_1` Q851：gold 要 constructorStandings.position，Pontis 输出 circuit 经纬度。
- `formula_1` Q855：gold 要 circuit.url，一行；Pontis 输出 race name 和 race.url 多行。
- `formula_1` Q894：gold 输出 milliseconds、driver、race，Pontis 少了 milliseconds。
- `formula_1` Q922：gold 输出 date 和 time，Pontis 只输出 time 且取了 2010s 多场 race。
- `formula_1` Q928：gold 输出 forename、surname、driverRef，Pontis 只输出 driverRef 且 rank/position 字段不同。
- `formula_1` Q958：gold 输出 driver name 和 fastestLapTime，Pontis 输出 lapTimes 中最快者的 name，少 fastestLapTime。
- `formula_1` Q970：gold 输出 driverId，Pontis 输出姓名、生日和 earliest_lap_time。
- `formula_1` Q973：gold 输出 driverId，Pontis 输出姓名和 latest_lap_time。
- `formula_1` Q985：gold 输出 driverId，Pontis 输出 forename 和 surname。
- `formula_1` Q986：gold 只输出 milliseconds，Pontis 额外输出 race name。
- `formula_1` Q1000：gold 输出 location，Pontis 输出 circuit name 和拼接 full_location。
- `formula_1` Q1010：gold 输出 Lewis Hamilton 的全部 lap time，Pontis 只输出最快一行并多 milliseconds。
- `formula_1` Q1011：gold 输出 forename、surname、driverId，Pontis 输出 forename、surname、time、milliseconds。
- `formula_1` Q1012：gold 只输出 position，Pontis 输出 circuit、lap、milliseconds、time、year、race 等多列。
- `student_club` Q1322：gold 用 EXCEPT 输出非 Meeting 的 event_name，Pontis 改成 count Meeting。
- `student_club` Q1366：gold 输出 member_id，Pontis 输出 first_name、last_name。
- `student_club` Q1399：gold 返回 `YES` 或 NULL，Pontis 返回 `Yes`/`No` 聚合标签。
- `student_club` Q1427：gold 输出 category 和 type，Pontis 少了 type。
- `student_club` Q1433：gold 输出 county，Pontis 输出 state。
- `student_club` Q1437：gold 输出 link_to_member 和 link_to_event，Pontis 输出姓名、event_link、event_name。
- `student_club` Q1450：gold 输出 city 和 county，Pontis 输出 city 和固定的 `United States`。
- `student_club` Q1451：gold 输出 member_id，Pontis 输出姓名且用 MAX(cost) 排序。
- `superhero` Q726：gold 输出 rank 列，Pontis 少了 HeightRank。
- `superhero` Q728：gold 输出 PopularityRank，Pontis 少 rank 列。
- `superhero` Q772：gold 输出 eye/hair/skin colour id，Pontis 输出 superhero_name 和颜色名称。
- `superhero` Q802：gold 只输出 superhero_name，Pontis 额外输出 height_cm 并返回并列最高。
- `superhero` Q812：gold 输出 superhero_name 且 LIMIT 5，Pontis 输出 full_name 且不 LIMIT。
- `thrombosis_prediction` Q1149：gold 只输出百分比，Pontis 额外输出 inpatient/outpatient 标签且比例未乘 100。
- `thrombosis_prediction` Q1177：gold 输出 `Normal`/`Abnormal`，Pontis 输出 T-CHO 和 `yes`/`no`。
- `thrombosis_prediction` Q1179：gold 输出 aCL IgA、IgG、IgM 三列，Pontis 只输出 aCL IgM。
- `thrombosis_prediction` Q1205：gold 输出 boolean，Pontis 输出 Date、UA 和 `Yes`/`No` 标签。
- `thrombosis_prediction` Q1212：gold 输出 Admission 原始 `+`/`-`，Pontis 输出 ID 和 inpatient/outpatient 文本。
- `thrombosis_prediction` Q1213：gold 输出 ID 和 normal/abNormal，Pontis 按患者聚合成 Yes/No。
- `thrombosis_prediction` Q1217：gold 只输出 normal/abnormal，Pontis 额外输出 ID、Birthday、Date、ALB。
- `thrombosis_prediction` Q1224：gold 输出 T-BIL、ID、SEX、Birthday，Pontis 少 T-BIL 且返回并列患者。
- `thrombosis_prediction` Q1225：gold 每个 ID 一行，Pontis 按 sex group_concat ID。
- `thrombosis_prediction` Q1236：gold 输出 ID 和 Admission 原始值，Pontis 把 Admission 映射成文本。
- `thrombosis_prediction` Q1241：gold 只输出低 PLT 数减高 PLT 数，Pontis 输出低、高、差值三列。
- `thrombosis_prediction` Q1242：gold 只输出 ID，Pontis 额外输出 SEX、Birthday、Diagnosis、Admission。
- `thrombosis_prediction` Q1268：gold 返回前三行 ID，Pontis 加 DISTINCT，可能丢重复行。
- `thrombosis_prediction` Q1269：gold 经 Patient+Laboratory 返回 ID，Pontis 只从 Laboratory 返回 DISTINCT ID。
- `toxicology` Q210：gold 输出 atom_id 和 atom_id2，Pontis 输出 atom_id 和 element。
- `toxicology` Q211：gold 输出 DISTINCT atom_id，Pontis 输出每个 bond 的 MIN/MAX atom_id。
- `toxicology` Q215：gold 输出 iodine 和 sulfur 两个计数，Pontis 输出合并后的一个 atom_count。
- `toxicology` Q216：gold 输出 atom_id 和 atom_id2，Pontis 只输出 atom_id。
- `toxicology` Q217：gold 输出 connected.atom_id 和 atom_id2，Pontis 输出 atom_id 和 element。
- `toxicology` Q236：gold 输出 bond_type、atom_id、atom_id2，Pontis 额外输出 bond_id 且 group by bond。
- `toxicology` Q237：gold 输出 molecule_id 和 YES/NO，Pontis 额外输出 label 和 carcinogenic 文本。
- `toxicology` Q248：gold 输出 atom_id 和 atom_id2，Pontis 输出 atom_id 和 element。
- `toxicology` Q250：gold 只输出 molecule_id，Pontis 额外输出 double_bond_count 并返回并列。
- `toxicology` Q252：gold 输出 atom_id、atom_id2，Pontis 输出 connected atom 及 element。
- `toxicology` Q253：gold 输出 DISTINCT element，Pontis 输出 bond_id 和两个 element 列。
- `toxicology` Q267：gold 输出 molecule_id、bond_type，Pontis 额外输出 bond_id。
- `toxicology` Q277：gold 单列返回两个 element 行，Pontis 两列返回一行。
- `toxicology` Q280：gold 输出 label 原始 `+`/`-`，Pontis 输出 carcinogenic/non-carcinogenic 文本。
- `toxicology` Q281：gold 只输出 DISTINCT element，Pontis 额外输出 count 并排序。
- `toxicology` Q285：gold 单列 element，Pontis 输出 element_1、element_2 两列。
- `toxicology` Q296：gold 输出 molecule_id、bond_id、atom_id，Pontis 只输出 molecule_id。
- `toxicology` Q305：gold 输出 bond_id、atom_id、atom_id2，Pontis 额外输出 molecule、bond_type、element 列。
- `toxicology` Q306：gold 每行 molecule_id、element，Pontis group_concat 元素成一行。

### 行粒度 / 去重 / 明细行 vs 唯一实体

- `california_schools` Q27：gold 用 LEFT JOIN 保留没有 SAT 行的学校，Pontis 用 INNER JOIN 丢掉这类学校。
- `california_schools` Q53：gold 返回 Fresno 每所学校的 `NumTstTakr` 明细，Pontis 汇总成 SUM。
- `california_schools` Q54：gold 只查 Adm1 槽位，Pontis 扩到 Adm1/Adm2，候选集合变大。
- `card_games` Q346：gold 按 legalities join 后明细返回，Pontis `GROUP BY` 去重。
- `card_games` Q351：gold 不去重地返回 foreign_data join 结果，Pontis `DISTINCT` 压成唯一 name。
- `card_games` Q354：gold 是 `COUNT(type)` 明细计数，Pontis 改成 `COUNT(DISTINCT types)`。
- `card_games` Q363：gold count distinct card id，Pontis count legalities join 后的行。
- `card_games` Q381：gold 返回所有匹配 artist 行，Pontis `DISTINCT` 去重。
- `card_games` Q382：gold 返回所有匹配 name 行，Pontis `DISTINCT` 去重。
- `card_games` Q383：gold count join 后的 card id 行，Pontis count distinct card id。
- `card_games` Q392：gold `DISTINCT name ORDER BY ruling date LIMIT 3`，Pontis 先按 name 分组再取 `MIN(date)`，候选粒度改变。
- `card_games` Q397：gold 保留所有 manaCost 行，Pontis `DISTINCT` 去重。
- `card_games` Q452：gold `DISTINCT name`，Pontis 不去重，重复行口径不同。
- `card_games` Q458：gold count 匹配 black border/arena,mtgo 的 card 行，Pontis count distinct artist。
- `card_games` Q499：gold count distinct set translation，Pontis count translation 行。
- `card_games` Q500：gold count distinct translation，Pontis count translation 行。
- `card_games` Q515：gold 在 oldest mythic legal join 行上 `LIMIT 1`，Pontis 先定 oldest card 再返回所有 legal format。
- `card_games` Q517：gold 计数所有 legal status 行，Pontis 理解成“所有 format 都 legal”的唯一 card 条件。
- `card_games` Q520：gold 选 count 最少的一个 artist-format 行，Pontis 处理并列后返回该 artist 的所有 format。
- `codebase_community` Q546：gold 返回拥有 closed post 的 owner 明细行，Pontis 用 `IN DISTINCT` 压成唯一用户。
- `codebase_community` Q571：gold 在 votes-posts join 后算比值，Pontis 分别独立计 posts/votes，join 粒度不同。
- `codebase_community` Q590：gold 返回所有最低 views 用户，Pontis `LIMIT 1`。
- `codebase_community` Q620：gold 返回每个用户的 Views 明细，Pontis SUM 成总 views。
- `codebase_community` Q621：gold 返回 lowest reputation 用户获得的 badge 明细，Pontis `DISTINCT` 去重 badge name。
- `codebase_community` Q672：gold 按 posts join 后的行计数，Pontis 先压成唯一 user。
- `codebase_community` Q700：gold count votes 行，Pontis count distinct PostId。
- `codebase_community` Q708：gold 保留每条含网址 comment 的用户行，Pontis 用 IN 子查询去重用户。
- `toxicology` Q269：gold 统计含 iodine 分子的所有 bond 行，Pontis 只统计直接连接 iodine atom 的 distinct bond。
- `debit_card_specializing` Q1496：gold 在 customer 粒度取最低 consumption，Pontis 按 segment 聚合后排序。
- `debit_card_specializing` Q1505：gold count 月消费记录行，Pontis count distinct customer。
- `debit_card_specializing` Q1525：gold 按交易记录行计算 EUR 比例，Pontis 按 distinct customer 计算。
- `european_football_2` Q1023：gold count Player_Attributes.id 行，Pontis count distinct player_api_id。
- `european_football_2` Q1063：gold 返回 Aaron Doran 的所有 potential 记录，Pontis 只取最新一条。
- `european_football_2` Q1080：gold count 属性记录行，Pontis count distinct player。
- `european_football_2` Q1086：gold 返回所有 heading_accuracy 记录，Pontis 只取最新记录。
- `european_football_2` Q1140：gold 返回所有 sprint/agility/acceleration 记录，Pontis 只取最新记录。
- `financial` Q109：gold 直接按 client district 统计女性，Pontis 经 disp/account 分支后变成账户持有人粒度。
- `financial` Q128：gold 直接按 client district 统计女性，Pontis 经 OWNER disposition 过滤。
- `financial` Q132：gold 包含所有 male borrower disposition 行，Pontis 限定 OWNER。
- `financial` Q135：gold count negative-balance credit-card transaction 行，Pontis count distinct account。
- `financial` Q150：gold count 匹配交易行，Pontis count 有匹配交易的 account。
- `financial` Q152：gold 在 account-joined district 行上平均，Pontis 先去重 district 再平均。
- `financial` Q185：gold 按 client district 行计数，Pontis 通过 disp/account 统计 distinct client。
- `financial` Q186：gold 用 account-disp-client join 行作分母，Pontis 直接用 disp/account 分母。
- `formula_1` Q891：gold 统计 driverStandings 行，Pontis 额外要求 driver 出现在 results。
- `formula_1` Q957：gold count Italian result rows with NULL time，Pontis count distinct drivers。
- `formula_1` Q963：gold count French lap rows，Pontis count distinct drivers。
- `formula_1` Q974：gold 返回所有 fastestLapTime 非空年份，Pontis 只取一条最快记录。
- `thrombosis_prediction` Q1169：gold count Laboratory 行，Pontis count distinct patient ID。
- `thrombosis_prediction` Q1174：gold count lab-patient join 行，Pontis 用 ID IN 压成 distinct lab/patient 口径。
- `thrombosis_prediction` Q1203：gold count female laboratory rows，Pontis count distinct patient ID。
- `thrombosis_prediction` Q1218：gold 计算 lab-row percentage，Pontis 计算 distinct patient percentage。
- `thrombosis_prediction` Q1222：gold count male laboratory rows，Pontis count distinct patient。
- `thrombosis_prediction` Q1227：gold 对 laboratory rows 对应年龄求平均，Pontis 对 distinct patient ages 求平均。
- `thrombosis_prediction` Q1252：gold count patient-lab join rows，Pontis 用 EXISTS 统计 patient。
- `thrombosis_prediction` Q1254：gold count patient-lab rows，Pontis count distinct patient。
- `thrombosis_prediction` Q1256：gold count laboratory rows，Pontis count distinct ID。
- `thrombosis_prediction` Q1267：gold count lab-exam join rows，Pontis 用 INTERSECT 压成 distinct ID。
- `thrombosis_prediction` Q1278：gold count lab rows，Pontis count distinct admitted ID。
- `thrombosis_prediction` Q1280：gold count lab rows，Pontis 用 EXISTS 统计 patient。
- `thrombosis_prediction` Q1283：gold count lab rows，Pontis count distinct patient。
- `thrombosis_prediction` Q1286：gold count lab rows，Pontis count distinct admitted ID。
- `thrombosis_prediction` Q1287：gold count lab rows，Pontis count distinct outpatient ID。
- `thrombosis_prediction` Q1289：gold count lab rows，Pontis count distinct SJS patient。
- `thrombosis_prediction` Q1291：gold count lab rows，Pontis count distinct male patient。
- `thrombosis_prediction` Q1295：gold count lab-exam join rows，Pontis 用 EXISTS 统计 patient。
- `thrombosis_prediction` Q1297：gold count lab-exam join rows，Pontis 用 IN/EXISTS 统计 patient。
- `thrombosis_prediction` Q1298：gold count lab-exam join rows，Pontis count distinct ID。
- `thrombosis_prediction` Q1299：gold count exam-lab rows，Pontis count distinct ID。
- `thrombosis_prediction` Q1302：gold count patient-lab-exam rows，Pontis 用 EXISTS 统计 patient。
- `thrombosis_prediction` Q1304：gold count lab-exam rows，Pontis count distinct ID。
- `thrombosis_prediction` Q1305：gold count lab rows，Pontis count distinct admitted ID。
- `thrombosis_prediction` Q1306：gold count lab rows，Pontis count distinct SLE patient。
- `thrombosis_prediction` Q1308：gold count lab rows，Pontis 用 EXISTS 统计 patient。

### 公式 / 聚合 / 排序候选口径

- `california_schools` Q15：题目问 district，但 gold 按 school-level SAT 行排序取对应 district，Pontis 切到 `rtype='D'` 的 district SAT 行。
- `california_schools` Q25：gold 先按 `District Name LIKE 'Riverside%'` 分组算平均；Pontis 用 county、单行 AvgScrMath 和 schools funding 字段。
- `california_schools` Q37：gold 直接按 excellence rate 升序取最低，Pontis 增加分母大于 0/null 排序处理，最低候选可能改变；列顺序也不同。
- `california_schools` Q41：gold 在 virtual school 集合内做 county rank，Pontis 先对所有学校 rank 再过滤 virtual。
- `california_schools` Q65：gold 分母是 non-locally-funded charter，Pontis 分母用 Santa Clara 全部 funding 非空学校，比例口径不同。
- `card_games` Q349：gold 的“most ruling information”实际按 promo artist/card 组合的特殊聚合取一行，Pontis 按 rulings 数直接排序。
- `card_games` Q352：gold 在 cards-foreign_data join 行上算比例，Pontis 用 foreign_data 计数除以 cards 总数。
- `card_games` Q371：gold 分母是 Story Spotlight cards 与 foreign_data join 后的行，Pontis 分母用 Story Spotlight card 数。
- `card_games` Q398：gold/evidence 的 unconverted mana 指 raw `manaCost` 列，Pontis 改成 `SUM(convertedManaCost)`。
- `card_games` Q403：gold percentage 来自 foreign_data 自身分母，Pontis 用 cards 分母并按 card name 分组。
- `card_games` Q416：gold 分母是 cards-foreign_data join 行，Pontis 分母用 cards 中 unknown power card 数。
- `card_games` Q417：gold 在 expansion sets 内算 Japanese language 占比，Pontis 在 Japanese translations 内算 expansion 占比，分子分母反了。
- `card_games` Q433：gold 在 sets-set_translations join 上算 online-only Chinese Simplified set 占比，Pontis 用 cards-foreign_data。
- `card_games` Q506：gold 以有 Japanese set translation 的 sets 为单位看 `isNonFoilOnly`，Pontis 用 cards/foreign_data 和 foil 字段推导。
- `card_games` Q507：gold 以有 Portuguese set translation 的 sets 为单位看 `isOnlineOnly`，Pontis 用 cards/foreign_data。
- `card_games` Q523：gold 按 sets.id 与 set_translations.id 的特殊公式/分组求值，Pontis 用正常 yearly count 和 foreign_data common language。
- `card_games` Q484：gold 返回 Coldsnap Italian card names 并按 convertedManaCost 降序，Pontis 只返回最高 convertedManaCost 的 Italian name。
- `codebase_community` Q587：gold 输出 `AVG(ViewCount), Title, Text` 并按 title/text 分组，Pontis 按 post/comment id 分组且列顺序不同。
- `codebase_community` Q595：gold 用 `COUNT(DISTINCT PostHistoryTypeId)=1`，Pontis 用每个 post 只有一条 history 的口径。
- `codebase_community` Q604：gold 直接 `AVG(UpVotes), AVG(Age)`，Pontis 手写 sum/count，可能改变 NULL 处理。
- `codebase_community` Q639：gold 经 postHistory 和 tags.ExcerptPostId 算比例，Pontis 用 posts.OwnerUserId 与 raw Tags 字符串。
- `codebase_community` Q687：gold 按 post 分组并用 `SUM(score)` 排序，Pontis 先选 max-score post 再数 comments。
- `financial` Q101：gold `ORDER BY date ASC LIMIT 1` 只取一个最早交易账户，Pontis 用 `MIN(date)` 返回所有同日账户。
- `financial` Q131：gold 的 highest active loan 按 `SUM(loan.amount)` 排序，Pontis 按 active loan 数量排序。
- `financial` Q141：gold 按 district 汇总 1997 年交易金额后 `HAVING SUM(amount)>10000`，Pontis 过滤单笔交易 `amount>10000`。
- `debit_card_specializing` Q1481：gold 先按 year/month aggregate transaction amount，Pontis 按 customer/card consumption 口径聚合。
- `debit_card_specializing` Q1482：gold 在 transaction amount 上求平均，Pontis 在 customer consumption 上求平均。
- `debit_card_specializing` Q1490：gold 直接按 year/month transaction amount 聚合，Pontis 加 card type/customer 条件并改用 avg consumption。
- `debit_card_specializing` Q1498：gold 按 transaction amount 求 max，Pontis 使用 customer consumption。
- `debit_card_specializing` Q1510：gold 用 transaction amount 求 merchant income，Pontis 用 customer consumption。
- `debit_card_specializing` Q1511：gold 分母是 transaction 行，Pontis 改成客户或卡片集合。
- `debit_card_specializing` Q1529：gold 对 transaction amount 求和/排序，Pontis 用 consumption 指标。
- `debit_card_specializing` Q1531：gold 按 transaction amount 计算，Pontis 用 customer consumption 或 customer-level 聚合。
- `european_football_2` Q1026：gold `ORDER BY COUNT ASC LIMIT 1` 取一个最少结果，Pontis 用 HAVING 返回全部并列最少。
- `european_football_2` Q1029：gold 按属性表平均 crossing，Pontis 先取每个球员最新/唯一值。
- `european_football_2` Q1037：gold 按属性记录计算平均，Pontis 按球员去重后计算。
- `european_football_2` Q1093：gold 直接按属性记录排序，Pontis 先做球员级聚合。
- `european_football_2` Q1094：gold 在属性记录上求 top，Pontis 取最新球员状态。
- `european_football_2` Q1107：gold 按属性记录聚合，Pontis 按球员级别聚合。
- `european_football_2` Q1118：gold 对 league/country season 结果直接聚合，Pontis 引入不同候选过滤。
- `european_football_2` Q1135：gold 按属性记录求比值，Pontis 先去重球员。
- `european_football_2` Q1136：gold 按 Player_Attributes 行求平均，Pontis 按 distinct player 聚合。
- `european_football_2` Q1137：gold 按属性行统计，Pontis 按 player 去重。
- `european_football_2` Q1148：gold 按 match/team 直接聚合，Pontis 先转成 team-level 候选。
- `financial` Q94：gold 用排序表达式取极值，Pontis 把极值拆成硬条件。
- `financial` Q95：gold 按 loan amount 排序取最高/最低，Pontis 先求极值条件后过滤。
- `financial` Q115：gold 用 account/order 行聚合，Pontis 改成 client/account 业务口径。
- `financial` Q145：gold 对 transaction rows 的 amount 计算，Pontis 改成 account/client 口径。
- `financial` Q189：gold 用排序表达式取极值，Pontis 把极值拆成子查询等值条件。
- `formula_1` Q847：gold 按结果表的 position/order 排名，Pontis 使用不同 ranking/status 口径。
- `formula_1` Q879：gold 按 constructor result/standings 聚合，Pontis 改用 race result 口径。
- `formula_1` Q930：gold 按 race/result row 求 aggregate，Pontis 改成 driver-level 统计。
- `formula_1` Q931：gold 排序时保留 NULL/文本时间口径，Pontis 额外做 NULL/数值处理。
- `formula_1` Q943：gold 按 result rows 的原始 rank 口径聚合，Pontis 用 standings/points 口径。
- `formula_1` Q975：gold 按原始 fastestLapTime 文本取值，Pontis 解析成时间/毫秒后排序。
- `formula_1` Q976：gold 返回 first-lap raw rows，Pontis 按 driver 取最快/唯一行。
- `formula_1` Q979：gold 用 results/lapTimes 原始记录聚合，Pontis 改成 driver/race 级聚合。
- `formula_1` Q1004：gold 按原始 result rows 计算，Pontis 用更业务化的排名/完赛口径。
- `student_club` Q1360：gold 百分比公式分母与 Pontis 的活动/成员口径不同。
- `student_club` Q1421：gold 按 budget/expense 原始金额聚合，Pontis 改用不同业务实体。
- `student_club` Q1442：gold percentage denominator 与 Pontis 选择的候选集合不同。
- `student_club` Q1453：gold 按原始 event/member row 聚合，Pontis 改成去重实体聚合。
- `student_club` Q1454：gold 按 expense/budget rows 聚合，Pontis 改成类别或成员口径。
- `student_club` Q1458：gold 百分比公式分母与 Pontis 的 membership/event 口径不同。
- `superhero` Q736：gold `ORDER BY ... LIMIT 1` 取一个 top，Pontis 返回全部并列或改动候选。
- `superhero` Q741：gold 按原始 power/attribute rows 聚合，Pontis 改成 hero-level 口径。
- `superhero` Q743：gold 用原始属性字段公式，Pontis 改成更业务化的属性组合。
- `superhero` Q788：gold 排序候选直接来自原表，Pontis 做了额外聚合或过滤。
- `superhero` Q794：gold `ORDER BY ... LIMIT 1`，Pontis 返回并列最大/最小候选。
- `superhero` Q720：gold 按 full_name 去重分组，Pontis 按 hero id 分组；同名英雄时粒度不同。
- `superhero` Q805：gold 排除 `full_name IS NULL`，Pontis 未过滤空 full_name。
- `superhero` Q832：gold `ORDER BY ... LIMIT 1`，Pontis 返回并列候选。
- `thrombosis_prediction` Q1150：gold 百分比乘 100，Pontis 输出 0.x 比例。
- `thrombosis_prediction` Q1168：gold 按 lab 检查记录聚合，Pontis 按 patient 聚合。
- `thrombosis_prediction` Q1185：gold 用原始检查行求平均/比例，Pontis 用患者去重口径。
- `thrombosis_prediction` Q1189：gold 直接在 Laboratory 行上聚合，Pontis 加 patient-level 分组。
- `thrombosis_prediction` Q1196：gold 按检查记录计算，Pontis 按患者实体计算。
- `thrombosis_prediction` Q1219：gold formula 直接基于检查行，Pontis 转成患者集合。
- `thrombosis_prediction` Q1243：gold 按 examination/lab rows 聚合，Pontis 改成 patient-level 条件。
- `thrombosis_prediction` Q1282：gold 按 Laboratory 行计算百分比，Pontis 按 distinct patient 计算。
- `toxicology` Q197：gold 按 atom/bond rows 聚合，Pontis 改成 molecule-level 口径。
- `toxicology` Q198：gold 按 connected/bond rows 计算，Pontis 按 molecule 或 atom 去重。
- `toxicology` Q212：gold formula 直接基于 atom/bond 行，Pontis 改成 molecule-level 汇总。
- `toxicology` Q219：gold 按原始 bond/atom 行聚合，Pontis 改用 distinct molecule。
- `toxicology` Q251：gold 按 connected rows 聚合，Pontis 改成 atom/molecule 去重。
- `toxicology` Q254：gold 对原始原子/键记录计算，Pontis 改成分子级指标。
- `toxicology` Q263：gold 按 bond rows 计数，Pontis 改成 molecule-level。
- `toxicology` Q286：gold 按 connected rows 计算，Pontis 改成 distinct molecule/atom。
- `toxicology` Q298：gold 百分比或计数分母基于原始记录，Pontis 用去重实体分母。
- `toxicology` Q310：gold 按 atom/bond rows 统计，Pontis 改成 molecule 粒度。
- `toxicology` Q317：gold 按原始 connected/bond rows 聚合，Pontis 改用去重实体。
- `toxicology` Q319：gold 按 raw bond/atom 行计算，Pontis 改成 molecule-level。
- `toxicology` Q324：gold 百分比乘 100，Pontis 输出 0.x 比例。
- `toxicology` Q330：gold 按原始行聚合，Pontis 改成去重实体口径。

### 精确条件 / 过滤范围 / 值解释

- `california_schools` Q7：gold 不限制 `rtype`，Pontis 加了 `rtype='S'`。
- `california_schools` Q16：题面说 Alameda，gold 却过滤 Lake；Pontis按题面走，且用 EXISTS 改变计数粒度。
- `california_schools` Q28：gold 用 schools.FundingType，Pontis 用 frpm.`Charter Funding Type`。
- `california_schools` Q43：gold 通过 schools 表取 county 且只过滤 AvgScrMath 非空，Pontis 用 satscores.cname 并限制 `rtype='S'`。
- `card_games` Q376：gold 要 `keywords = 'Flying'`，Pontis 用 `LIKE '%Flying%'` 且 distinct。
- `card_games` Q391：gold 要 `originalType = 'Artifact'`，Pontis 用 `LIKE '%Artifact%'`。
- `card_games` Q393：gold 的 non-powerful 是任一 kingdom id/foil id 为空，Pontis 只取 foil id 非空且 kingdom id 为空。
- `card_games` Q410：gold 只要求 pauper format 和 paper availability，Pontis 额外加 `status='Legal'` 且 availability 用 LIKE。
- `card_games` Q412：gold 用 `types='Creature'`，Pontis 用 `type LIKE '%Creature%'`。
- `card_games` Q447：gold type 只等于 `commander`，Pontis 放宽成 `expansion` 或 `commander`。
- `card_games` Q454：gold 用 `power LIKE '%*%' OR power IS NULL` 的 unknown power 口径，Pontis 用 `power='*' OR NULL`。
- `card_games` Q357：gold 要 `promoTypes IS NOT NULL`，Pontis 未加非空过滤且 `DISTINCT`。
- `card_games` Q359：gold 用 `originalType IS NOT NULL` 过滤，Pontis 换成 `isReprint = 0`。
- `card_games` Q529：gold 只在 Korean translation 行上加 `language NOT LIKE '%Japanese%'`，Pontis 解释成“有 Korean 且没有 Japanese translation”。
- `codebase_community` Q533：gold 用 `date(LastAccessDate) > '2014-09-01'`，Pontis 用 raw timestamp 比较，边界日口径不同。
- `codebase_community` Q569：gold 匹配所有 title LIKE data visualization 的 posts，Pontis 额外固定 `p.Id = 44`。
- `codebase_community` Q593：gold 实际统计 Teacher/Supporter 任一 badge 且 Location 精确等于 New York，Pontis 要两种 badge 都有并用 LIKE。
- `codebase_community` Q597：gold `Location = 'India'`，Pontis `LIKE '%India'`。
- `codebase_community` Q625：gold `Location = 'New York'`，Pontis `LIKE '%New York%'`。
- `codebase_community` Q710：gold 用精确 title/body 条件，Pontis 改成更宽的文本匹配。
- `european_football_2` Q1041：gold 的 league/season 条件更窄，Pontis 放宽到相邻候选集合。
- `european_football_2` Q1061：gold `Adam %` 保留空格边界，Pontis 用 `Adam%` 放宽匹配。
- `financial` Q179：gold 使用精确 district/loan 条件，Pontis 额外加入账户角色限制。
- `financial` Q192：gold status 包含 `C,D`，Pontis 只过滤 `C`。
- `formula_1` Q860：gold 用原始 nationality/name 条件，Pontis 改成更宽的参赛实体条件。
- `formula_1` Q876：gold 的 did not finish 用 `time IS NULL`，Pontis 使用 status 文本/状态映射。
- `formula_1` Q915：gold 保留 NULL/原始排序候选，Pontis 加了非空或数值过滤。
- `formula_1` Q927：gold 的结果候选不排除 NULL，Pontis 额外过滤 NULL。
- `formula_1` Q937：gold 使用精确 race/constructor 条件，Pontis 加入额外赛季或状态条件。
- `formula_1` Q956：gold 直接过滤 result time/status，Pontis 使用更业务化的完赛条件。
- `formula_1` Q996：gold 对 race/circuit 条件取字面匹配，Pontis 扩展到相关国家/地点候选。
- `student_club` Q1387：gold 只按 expense/member 路径过滤，Pontis 额外加 `m.position = 'Treasurer'`。
- `superhero` Q827：gold 不过滤 height=0，Pontis 加 `height_cm > 0`。
- `thrombosis_prediction` Q1163：gold 用检查值的字面阈值，Pontis 做了额外异常/非空过滤。
- `thrombosis_prediction` Q1170：gold 按原始 diagnosis/lab 条件过滤，Pontis 加 patient-level 限制。
- `thrombosis_prediction` Q1193：gold 只要求指定检查条件，Pontis 扩大到相关检查组合。
- `thrombosis_prediction` Q1200：gold 条件直接落在 Laboratory 字段，Pontis 先转成 patient condition。
- `thrombosis_prediction` Q1211：gold 使用给定边界条件，Pontis 改动了边界包含关系。
- `thrombosis_prediction` Q1239：gold 要 `> 2`，Pontis 使用 `>= 2` 或等价放宽。
- `thrombosis_prediction` Q1260：gold 按 `RF` 原始字段/值过滤，Pontis 解析成更宽的 rheumatoid factor 条件。
- `thrombosis_prediction` Q1261：gold 用原始检查代码条件，Pontis 替换成语义相近检查集合。
- `thrombosis_prediction` Q1264：gold 条件只覆盖一个检查值，Pontis 扩成多检查/患者条件。
- `thrombosis_prediction` Q1270：gold 按原始 medication/diagnosis 字段过滤，Pontis 加额外 patient 状态条件。
- `thrombosis_prediction` Q1277：gold 使用 Laboratory 原始条件，Pontis 加了 admission/outpatient 等额外限制。
- `toxicology` Q214：gold 按原始 element/bond 条件过滤，Pontis 加 molecule-level 约束。
- `toxicology` Q247：gold 用精确 element 条件，Pontis 扩展到相关 atom/bond 条件。
- `toxicology` Q259：gold 只过滤指定 bond/element，Pontis 加额外 molecule label 条件。
- `toxicology` Q260：gold 按原始 atom/bond 条件计数，Pontis 改成 molecule 条件并改变范围。
- `toxicology` Q290：gold 使用精确元素/键类型条件，Pontis 放宽或替换为语义相近条件。
- `toxicology` Q311：gold 只按题面指定条件过滤，Pontis 加 molecule label 或额外非空条件。

### 原始字段 vs 自行加工 / 原样返回

- `california_schools` Q71：gold 返回 frpm.`District Code` 原始列，Pontis 从 schools.CDSCode 用 `SUBSTR` 推导。
- `california_schools` Q81：gold 返回 frpm.`Low Grade`，Pontis 从 schools.GSoffered 字符串解析最低年级。
- `california_schools` Q85：gold 按 evidence 公式计算 percent，Pontis 直接返回已有 percent 字段。
- `card_games` Q399：gold 返回 raw `subtypes, supertypes` 两列，Pontis 拼接并拆分成单个 type 列。
- `card_games` Q425：gold 虽然题面说 card numbers，但实际返回 id；Pontis 返回 number。
- `card_games` Q441：gold 返回 set_translations.setCode，Pontis 返回 sets.code。
- `card_games` Q443：gold 返回 set_translations.setCode，Pontis 返回 sets.code。
- `codebase_community` Q630：gold 返回 posts.Tags 原始字符串，Pontis 拆成 tag 表里的单个 TagName。
- `codebase_community` Q637：gold 返回 posts.Tags 原始字符串，Pontis 用字符串函数拆成 tag_name。
- `codebase_community` Q692：gold 返回 raw Text/Body 字段，Pontis 对文本做截取或拼接。
- `european_football_2` Q1031：gold 用简单 year 差并只返回年龄，Pontis 多返回 player name 且用更合理年龄算法。
- `financial` Q174：gold 返回原始 account/order 字段，Pontis 做了拼接或派生解释。
- `financial` Q194：gold 用当前 year 算年龄，Pontis 用 card issued date 算年龄。
- `formula_1` Q871：gold 返回原始 time/status 字段，Pontis 输出解析后的数值或解释。
- `formula_1` Q873：gold 返回原始 race/result 字段，Pontis 拼接展示字段。
- `formula_1` Q889：gold 直接返回原始 constructor/driver 字段，Pontis 输出派生 full name。
- `formula_1` Q936：gold 返回原始 result time，Pontis 转成 milliseconds 或格式化时间。
- `formula_1` Q955：gold 使用原始 result status/time 字段，Pontis 做了语义解释。
- `formula_1` Q987：gold 返回原始 duration/time 文本，Pontis 转成数值时间。
- `formula_1` Q1005：gold 返回原始 fastestLapTime，Pontis 转成 milliseconds 或解析时间。
- `formula_1` Q1006：gold 返回原始 duration 字段，Pontis 输出解析/换算结果。
- `formula_1` Q1007：gold 返回原始 fastestLapTime 文本，Pontis 输出数值化时间。
- `thrombosis_prediction` Q1197：gold 返回原始 Laboratory 字段，Pontis 做了代码解释或派生字段。
- `thrombosis_prediction` Q1204：gold 用原始日期/天数字段口径，Pontis 用 julianday 等函数重算。
- `thrombosis_prediction` Q1249：gold 按 `U-PRO` 原始检查字段输出/过滤，Pontis 改成解释后的尿蛋白口径。
- `thrombosis_prediction` Q1285：gold 返回原始检查值，Pontis 返回解释/派生结果。

### Schema / table / role linking

- `california_schools` Q10：gold 直接 `satscores.cds = frpm.CDSCode`，Pontis 试图给 `cds` 补 0，join key 处理错。
- `california_schools` Q11：gold 通过 schools-frpm join 限定学校集合，Pontis 只查 frpm，可能多出无 school 匹配行。
- `california_schools` Q19：gold 用 CDSCode join，Pontis 用 school name = sname，join path 不稳。
- `california_schools` Q51：gold 用 CDSCode join 后取 lowest reading 的 school，Pontis 用 school name = sname 并用 `MIN()` 等值匹配，join path 和 top 候选都变了。
- `california_schools` Q26：gold 用 frpm.`School Type` 和 `Free Meal Count`，Pontis 用 schools.EILCode 和 frpm.`FRPM Count`。
- `california_schools` Q83：gold 需要 schools+frpm、Magnet、GSoffered、NSLP Provision Status，Pontis 只用 schools.GSserved 分组。
- `card_games` Q360：gold 通过 set_translations.id/cards.id 的 BIRD 口径取 language，Pontis 走 cards-set_translations 正常 setCode 路径并多输出列。
- `card_games` Q387：gold 用 cards.id in set_translations where setCode='OGW'，Pontis 用 cards.setCode='OGW' 并返回 name。
- `card_games` Q388：gold 查 set_translations 的 id/language，Pontis 查 cards+foreign_data。
- `card_games` Q400：gold 直接从 set_translations 取 Spanish setCode，Pontis 从 cards+foreign_data 推 setCode。
- `card_games` Q406：gold 需要 rulings 和 legalities join，Pontis 只查 cards.types。
- `card_games` Q408：gold 返回 rulings.text，Pontis 在 cards.text 上查并返回 count；表和输出都错。
- `card_games` Q428：gold 过滤 set_translations.id=5，Pontis 过滤 sets.id=5。
- `card_games` Q431：gold 通过 set_translations 判断 Japanese set，Pontis 通过 cards+foreign_data 判断 Japanese card。
- `card_games` Q444：gold 返回 cards.name/cards.type，Pontis 返回 foreign_data.name/type 且固定 Chinese Simplified。
- `card_games` Q463：gold 统计 set_translations.translation，Pontis 统计 card foreign_data。
- `card_games` Q465：gold 查 set_translations 是否有 Korean set version，Pontis 查 card foreign_data。
- `card_games` Q482：gold 返回 cards.type，Pontis 返回 foreign_data.type。
- `codebase_community` Q581：gold 的 editor 实际取 post owner，Pontis 按 LastEditorUserId 取 editor。
- `codebase_community` Q582：evidence 说 last edited，但 gold 取 OwnerUserId；Pontis 按 evidence 走 LastEditorUserId。
- `codebase_community` Q584：gold 把 postHistory.Comment 当 comment，Pontis 查 comments 表。
- `codebase_community` Q594：gold 通过 comments.PostId=1 找 comment user，Pontis 找 posts.OwnerUserId。
- `codebase_community` Q602：gold 返回 postHistory.PostId/UserId，Pontis 返回 posts.Id/OwnerUserId。
- `codebase_community` Q632：gold 通过 Harlan 的 postHistory 关联其 post 上的 votes，Pontis 查 Harlan 自己投出的 votes。
- `codebase_community` Q631：gold 通过 Harlan 的 postHistory 关联 post votes，Pontis 查 Harlan 自己投出的 votes。
- `codebase_community` Q640：gold 通过 postHistory.UserId 归属计算 view difference，Pontis 用 posts.OwnerUserId；同时 Mornington 大小写不一致。
- `codebase_community` Q646：gold 按 posts.Score>60 且 join 条件本身异常，Pontis 按 comments.Score>60。
- `codebase_community` Q656：gold 返回最高分 child post 自己 owner 的 display name，Pontis 返回 parent post owner。
- `codebase_community` Q667：gold 使用 postHistory/comment 相关路径，Pontis 走 posts/comments 的自然业务路径。
- `codebase_community` Q685：gold 的用户角色来自 postHistory，Pontis 使用 posts.OwnerUserId。
- `codebase_community` Q689：gold 在 comments/posts 之间选定特定 role，Pontis 选了另一端用户。
- `codebase_community` Q694：gold 使用 badges/postHistory 的关联口径，Pontis 改走 posts owner 路径。
- `debit_card_specializing` Q1477：gold 用 transaction/card 事实表路径，Pontis 选择 customer/card 派生路径。
- `debit_card_specializing` Q1504：gold 用 merchant/transaction role，Pontis 选成 customer/card role。
- `debit_card_specializing` Q1517：gold 要 transaction/card 字段，Pontis 用 customer segment 或 account 字段。
- `debit_card_specializing` Q1524：gold 从 transaction merchant 口径取值，Pontis 走 customer/consumption 口径。
- `european_football_2` Q1027：gold 用 Team/Match 的主客队 role，Pontis 选错 home/away 或 team_api_id。
- `european_football_2` Q1119：gold 使用 league/country join 字段，Pontis 选到 team/match 相邻字段。
- `european_football_2` Q1120：gold 的球员属性来自 Player_Attributes，Pontis 用 Player 主表字段。
- `european_football_2` Q1121：gold 使用 match 主客队字段，Pontis 选错 team role。
- `european_football_2` Q1126：gold 要 league/country 字段，Pontis 用 team/country 近似字段。
- `european_football_2` Q1131：gold 使用 Player_Attributes 的属性列，Pontis 使用 Player 或 match 侧字段。
- `european_football_2` Q1134：gold 把 6/23 当 Player_Attributes.id，Pontis 当 Player.id 后取最新属性记录。
- `financial` Q102：gold 直接走 account/district 字段，Pontis 选择 client/district 或 disposition 路径。
- `financial` Q130：gold 使用 district A 字段，Pontis 选错相邻 A 字段。
- `financial` Q142：gold 要 loan/account 事实路径，Pontis 选成 client/disposition 路径。
- `financial` Q168：gold 直接用 client.district_id，Pontis 经 account/disp 绕到账户 district。
- `financial` Q182：gold 使用 district 字段 A2/A3 的特定角色，Pontis 选错相邻字段。
- `formula_1` Q849：gold 用 race/result 事实表角色，Pontis 选到 driverStandings/constructorStandings。
- `formula_1` Q861：gold 使用 constructor result path，Pontis 改走 driver result path。
- `formula_1` Q887：gold 按 circuit name 排除，Pontis 按 circuitId 或 race 字段排除。
- `formula_1` Q892：gold 使用 result status/result 字段，Pontis 选到 standings 字段。
- `formula_1` Q893：gold 使用 raceId/resultId 对应事实表，Pontis 选错 standings 或 lapTimes。
- `formula_1` Q896：gold 的 team/constructor 来自 constructors，Pontis 用 results/driver role。
- `formula_1` Q897：gold 使用 race name/year 角色，Pontis 选到 circuit/location。
- `formula_1` Q902：gold 用 result position/status 字段，Pontis 用 standings rank。
- `formula_1` Q903：gold 从 lapTimes/results 取字段，Pontis 选成 races/constructor。
- `formula_1` Q905：gold 使用 driver result role，Pontis 选到 constructor result role。
- `formula_1` Q908：gold 使用 constructorStandings/result path，Pontis 使用 driverStandings。
- `formula_1` Q949：gold 字段来自 results.fastestLap，Pontis 去 lapTimes 取 lap。
- `formula_1` Q959：gold 直接返回 results.fastestLap，Pontis 去 lapTimes 里按毫秒重新找最快圈。
- `formula_1` Q950：gold 使用 results.fastestLapSpeed，Pontis 选到 lapTimes milliseconds。
- `formula_1` Q951：gold 使用 results.rank/fastestLap rank，Pontis 使用 finishing position。
- `formula_1` Q952：gold 用 results.fastestLapTime，Pontis 使用 lapTimes time/milliseconds。
- `formula_1` Q966：gold 使用 race name/year 字段，Pontis 走 circuit/country 字段。
- `formula_1` Q984：gold 要 constructor result，Pontis 选到 driver result。
- `formula_1` Q995：gold 用 race/circuit 的指定字段，Pontis 选到相邻 location/country。
- `student_club` Q1391：gold 使用 member/event attendance path，Pontis 选到 budget/expense path。
- `student_club` Q1404：gold 使用 budget.category，Pontis 选到 event.type 或 expense description。
- `student_club` Q1418：gold 用 attendance/member join，Pontis 走 event/budget join。
- `student_club` Q1419：question 问 category，gold 要 budget.category，Pontis 用 event.type。
- `student_club` Q1422：gold 使用 expense/budget 表，Pontis 选到 event/member 表。
- `student_club` Q1436：gold 要 attendance.link_to_event，Pontis 从 budget/event 推事件。
- `thrombosis_prediction` Q1175：gold 用 Laboratory 检查字段，Pontis 选到 Examination/Patient 相邻字段。
- `thrombosis_prediction` Q1186：gold 使用指定 lab item，Pontis 选了语义相近但不同的检查列。
- `thrombosis_prediction` Q1233：gold 使用 diagnosis/Admission 字段，Pontis 选到 Laboratory 条件。
- `thrombosis_prediction` Q1245：gold 用 Examination 结果字段，Pontis 选到 Laboratory 值字段。
- `thrombosis_prediction` Q1251：gold 使用 Patient diagnosis 字段，Pontis 使用 lab/exam 代理条件。
- `thrombosis_prediction` Q1273：gold 使用 Laboratory 原始字段，Pontis 选到 Examination 或 Patient 字段。
- `thrombosis_prediction` Q1300：gold 用指定 lab item，Pontis 选了相邻项目。
- `toxicology` Q207：gold 使用 connected.atom_id/atom_id2 指定方向，Pontis 选错 bond endpoint。
- `toxicology` Q257：gold 要 atom_id2 方向，Pontis 取 atom_id 或反向 endpoint。
- `toxicology` Q326：gold 使用 atom/connected 的指定 endpoint，Pontis 选成另一端。
- `toxicology` Q328：gold 从 atom 表取 element/id，Pontis 走 connected/bond 另一角色。
- `toxicology` Q335：gold 使用 connected 关系表字段，Pontis 选到 atom 表近似字段。
- `toxicology` Q337：gold 需要 bond endpoint role，Pontis 选错 atom_id 与 atom_id2。
- `toxicology` Q338：gold 使用 molecule/atom 直接路径，Pontis 走 bond/connected 派生路径。

### Gold / evidence / question 字面冲突或异常口径

- `card_games` Q342：evidence 说 Max(faceConvertedManaCost)，gold 却按 `ORDER BY faceConvertedManaCost LIMIT 1` 取最小；Pontis 按 max 理解。
- `card_games` Q446：question/gold 用 convertedManaCost=10，evidence 文本写 16；Pontis 按 evidence 的 16。
- `card_games` Q474：question/gold 是 baseSetSize < 100，evidence 错写 `< 10`；Pontis 按 evidence 的 10。
- `card_games` Q519：gold 从 set_translations.id in sets.id 查 Battlebond language，Pontis 在没有 translation 行时返回 English；这是 BIRD gold 的异常 set translation 口径。
- `codebase_community` Q608：question 时间是 19:25:47，evidence/gold 时间是 19:16:14；Pontis 按 question，gold 按 evidence。
- `codebase_community` Q635：question 说 more than 4 votes，evidence 却写 `PostId > 4`，gold 实际按 votes count HAVING；Pontis按 evidence 的 `p.Id > 4`。
- `codebase_community` Q709：question/evidence 说 comments with 0 score，gold 过滤 posts.Score=0；Pontis 按 comment score 执行。
- `european_football_2` Q1060：question/gold 与 evidence 的年份条件冲突，Pontis 按 evidence 的 `= 1990` 执行。
- `european_football_2` Q1113：question/evidence 对 league 或 season 的指向不一致，Pontis 跟随了另一侧文本。
- `financial` Q110：question/evidence 与 gold 的 district/region 口径不一致，Pontis 按题面文本执行。
- `financial` Q118：gold 使用异常的字段/条件组合，Pontis 选择了题面更自然的业务路径。
- `financial` Q144：question 描述和 gold 的状态/账户条件不完全一致，Pontis 按字面条件执行。
- `financial` Q171：evidence 指 north-east，gold 使用 east-north 相关字段/值，Pontis 跟随 evidence。
- `formula_1` Q998：question/evidence 对 race/circuit 条件有冲突，Pontis 选择了另一侧文本。
- `thrombosis_prediction` Q1199：question/evidence 与 gold 的检查字段口径不一致，Pontis 按更直观字段执行。
- `thrombosis_prediction` Q1247：gold 使用异常检查字段组合，Pontis 按题面语义选了相邻医学字段。
- `thrombosis_prediction` Q1248：gold 与 question 的检查项/阈值口径不一致，Pontis 按题面理解执行。
- `thrombosis_prediction` Q1265：evidence/gold 对诊断或检查条件有异常口径，Pontis 按另一侧文本执行。
- `thrombosis_prediction` Q1271：gold 采用不直观的 lab/exam 字段组合，Pontis 按题面语义选字段。
- `thrombosis_prediction` Q1274：evidence 要 `SSB IN ('negative','0')`，gold 写成 `SSB='negative' OR '0'` 的异常条件；Pontis 按 evidence 写。
- `thrombosis_prediction` Q1279：question 与 gold 的检查结果口径不一致，Pontis 按题面/字段说明执行。
- `thrombosis_prediction` Q1284：gold 使用异常条件组合，Pontis 选择了语义更直接的 patient/lab 条件。
- `toxicology` Q218：question 说不含 fluorine，gold 实际按非 fluorine 原子行计数，Pontis 按分子层面的“不含”理解。
- `toxicology` Q234：gold 的 atom/bond endpoint 口径与题面文字不自然，Pontis 选择了相反或更自然方向。
- `toxicology` Q271：gold 使用异常 bond/atom 条件组合，Pontis 按题面元素关系执行。
- `toxicology` Q309：question 指定的 molecule 与 gold 使用的 molecule 条件不一致，Pontis 按题面执行。

### 执行错误 / SQL 拼写错误

- `codebase_community` Q603：Pontis 使用不存在的 `CreaionDate`，并且路径从 posts.OwnerUserId 出发，不是 gold 的 postHistory.UserId。
- `codebase_community` Q642：Pontis 使用 posts 表和不存在的 `CreaionDate`；gold 统计 postHistory 的 date(CreationDate)。
- `codebase_community` Q652：Pontis 使用 posts.`CreaionDate`，并且从 users/posts 出发；gold 从 postHistory.UserDisplayName 和 badges.Date 出发。
- `codebase_community` Q662：Pontis SQL 有字段拼写或表别名错误，未能正常执行。
- `codebase_community` Q682：Pontis SQL 使用不存在字段或错误别名，执行失败。
- `codebase_community` Q683：Pontis 使用不存在的 `CreaionDate`，导致执行失败；同时公式口径也偏离。
- `codebase_community` Q696：Pontis SQL 的字段/别名引用错误，执行失败。
- `financial` Q165：Pontis SQL 输出/引用的交易字段不符合可执行 schema，执行失败或结果列异常。
- `formula_1` Q1014：Pontis race/circuit 字段引用或大小写不符合实际 schema，执行失败。
- `formula_1` Q1015：Pontis 把 Austrian Grand Prix 解析到 country/circuit 后字段引用异常，执行失败。
- `formula_1` Q1016：Pontis race/circuit 相关字段引用错误，执行失败。

### 疑似业务等价或需要复核评测差异

- `card_games` Q375：Pontis 与 gold 都是 cards.id where convertedManaCost=0，看起来业务等价。
- `card_games` Q378：Pontis 与 gold 都过滤两个 kingdom id 非空并返回 id，看起来业务等价。
- `card_games` Q380：Pontis 与 gold 都返回 frameVersion=2015 的 edhrecRank，看起来业务等价。
- `card_games` Q384：Pontis 从 foreign_data 出发查 legacy uuid/language，结果应接近 gold 的 cards-legalities-foreign_data join，需要复核是否有重复行差异。
- `card_games` Q435：Pontis 与 gold 都返回 black border card id，看起来业务等价。
- `card_games` Q518：Pontis 与 gold 都找 banned 最多 format 下的 banned card names，差异主要是 DISTINCT/order，业务上接近。
- `codebase_community` Q532：`substr(CreationDate,1,4)='2011'` 与 `strftime('%Y', CreationDate)='2011'` 应接近等价。
- `codebase_community` Q676：Pontis 与 gold 的过滤和返回对象接近，差异疑似主要来自排序/重复行，需要复核。
- `codebase_community` Q715：Pontis 与 gold 看起来业务等价，差异可能是重复行、排序或 null 处理。
- `european_football_2` Q1059：Pontis 与 gold 的核心条件接近，现有结果差异疑似来自执行/排序细节。
- `financial` Q127：Pontis 与 gold 业务条件接近，差异疑似来自 join 重复行或输出顺序。
- `thrombosis_prediction` Q1190：Pontis 与 gold 选取的患者/检查集合接近，需要复核是否只是重复行口径。
- `toxicology` Q205：Pontis 与 gold 都围绕含 carbon 的 molecule/atom，差异疑似来自 DISTINCT 或重复行口径。

## 分库分析

### california_schools

Gap 题：11。BIRD 风格 6，schema/linking 5。

主要问题：

- 输出列过宽：Q1 多返回 `School Name`；Q57 多返回 `cds`；Q88 列顺序/字段来源偏离。
- 额外过滤：Q7/Q15/Q57/Q88 加了 `rtype = 'S'/'D'`，gold 没有。
- 字段/关系 linking：Q10 用拼接 `0 || cds` 子查询而不是直接 join；Q19 用 school name 连接；Q71 从 `CDSCode` 推 district code，而 gold 用 `frpm.District Code`。
- 多槽位字段：Q36 少返回 AdmFName3/AdmLName3。

这个库的错误混合度较高。很多题不是不知道字段，而是 Pontis 觉得要更精确过滤或重构连接路径。

### card_games

Gap 题：25。BIRD 风格 23，schema/linking 2。

主要问题：

- 执行错误/中断很多：Q346, Q351, Q375, Q378, Q380, Q381, Q382, Q397, Q435, Q452, Q518。
- 百分比 denominator 错：Q352, Q371, Q416。Pontis 使用 cards 总数或子查询总数，gold 按 join 后行粒度。
- 输出形状错：Q459 多返回 `convertedManaCost` 且没 `LIMIT 1`；Q483 加 ruling join 和多余列；Q430 输出 set code，gold 要 set name/id。
- 条件过度解释：Q393, Q410, Q517。
- 表选择错：Q506 应在 `sets/set_translations`，Pontis 转到 `cards/foreign_data`。

这个库最大问题不是 schema 缺失，而是大表查询容易超时，以及 Pontis 对题面做了太多业务解释。

### codebase_community

Gap 题：17。BIRD 风格 15，schema/linking 2。

主要问题：

- 执行错误/中断：Q532, Q546, Q596, Q621, Q676, Q708, Q715。
- 原始字段 vs 结构化解析：Q630/Q637 gold 要 `posts.Tags` 原始字符串，Pontis 拆 tag。
- 输出形状错：Q599 用 `GROUP_CONCAT` 聚成一行，gold 每个 `PostHistoryTypeId` 一行；Q620 求 `SUM(Views)`，gold 返回每行 `Views`。
- 精确匹配变模糊匹配：Q597 `LIKE '%India'`；Q625 `LIKE '%New York%'`。
- 关系字段错：Q685 应用 `LastEditorUserId`，Pontis 用 `OwnerUserId`。
- evidence 冲突处理：Q608 题面时间和 evidence 时间不一致，gold 跟 evidence，Pontis 跟题面。

这个库的关键是“不要结构化解析 BIRD 要求返回的原始文本字段”，以及减少大表执行中断。

### debit_card_specializing

Gap 题：7。BIRD 风格 4，schema/linking 3。

主要问题：

- 聚合粒度错：Q1480 应按月份 `SUBSTR(Date,5,2)` 聚合，Pontis 按完整 yearmonth。
- 百分比分母错：Q1490/Q1525 Pontis 按 customer 去重或按 customer total，gold 按交易/月度明细行。
- 表/字段 linking：Q1504 应用 `transactions_1k` 的 `Price * Amount`，Pontis 用 `yearmonth.Consumption`；Q1524 nationality 实际来自 `gasstations.Country`，Pontis 用 `customers.Currency`。
- 输出过宽：Q1512 多返回 segment/currency/total_paid。
- 关系/时间来源：Q1517 earliest customer 应来自交易或 yearmonth 记录，Pontis 直接按 customer ID。

这个库的 gap 主要是交易明细行粒度和事实表选择。

### european_football_2

Gap 题：11。全部归为 BIRD 风格。

主要问题：

- 明细行 vs 最新属性/唯一球员：Q1063, Q1086, Q1140 取最新属性，gold 返回所有匹配属性行；Q1080/Q1136/Q1137 用 distinct/player-level 聚合，gold 按 `Player_Attributes` 行。
- evidence 文本要机械执行：Q1060 evidence 写错但 gold 是 `> 1990`，Pontis用了 `= 1990`。
- 字符串匹配过窄：Q1061 `Adam %` vs `Adam%`。
- top 题并列/集合：Q1026 用 HAVING 找全部最少，gold `ORDER BY COUNT ASC LIMIT 1`。
- 年龄计算输出形状：Q1031 多返回 player name 且用更合理年龄算法，gold 是简单 year 差。
- 执行错误：Q1059。

这个库说明 Pontis 会主动把球员属性题写成“最新球员状态”，但 BIRD gold 多数按属性表历史行。

### financial

Gap 题：15。BIRD 风格 6，schema/linking 9。

主要问题：

- 表路径 linking 错：Q109/Q128/Q168/Q185 应直接用 `client.district_id -> district`，Pontis 走 `account/disp`。
- A2/A3 字段混用：Q124 应用 A2，Pontis用 A3；Q133 输出字段也偏了。
- 排序式极值被拆成硬条件：Q95/Q189。
- 状态值漏掉：Q192 只用 `status='C'`，gold 要 `C,D`。
- 输出列过宽：Q165 选了 trans 多列，gold 只要 `trans_id`。
- 年龄参照点错：Q194 用 card issued date，gold 用 current year。
- 执行错误：Q127。

这是 gap 库里 schema/linking 占比最高的一个，重点是 district 字段和业务路径消歧。

### formula_1

Gap 题：20。BIRD 风格 14，schema/linking 6。

主要问题：

- 原始时间字段 vs 数值时间：Q963, Q975, Q987, Q1005, Q1007。Pontis 使用 milliseconds 或解析时间，gold 常按原始 `time/duration/fastestLapTime` 文本。
- 行粒度去重：Q957 count result rows 被写成 distinct drivers；Q976 first-lap raw rows 被写成每 driver 最快。
- 字段 linking：Q876 `did not finish` gold 用 `time IS NULL`；Q959 应用 `results.fastestLap`，Pontis 去 `lapTimes`。
- race/circuit linking：Q887 name not in 被写成 circuitId not in；Q1015/Q1016 Austrian Grand Prix 用 race name，Pontis按 country/circuit。
- 输出形状：Q1000 gold 只要 location，Pontis给 circuit/full_location。
- null/排序细节：Q915, Q927, Q931。

这个库的问题是 Pontis 会把 F1 时间字段和比赛实体做更合理的业务建模，但 BIRD gold 更机械。

### student_club

Gap 题：6。BIRD 风格 4，schema/linking 2。

主要问题：

- 百分比 denominator：Q1360/Q1442/Q1458。
- 额外业务条件：Q1387 加 `m.position = 'Treasurer'`，gold 只按 expense/member 路径。
- 字段/table linking：Q1419 问 category，gold 要 budget.category，Pontis 用 event.type；Q1436 要 attendance.link_to_event，Pontis 从 budget/event 推。

这个库小但典型：Pontis 会补业务角色和选择更自然路径，反而偏离 gold。

### superhero

Gap 题：5。全部归为 BIRD 风格。

主要问题：

- top/max/min 并列处理：Q736/Q794/Q802/Q832 Pontis 用 `WHERE value = MAX(...)` 返回并列或多列，gold `ORDER BY ... LIMIT 1`。
- 额外过滤：Q827 加 `height_cm > 0`，gold 没有。

这个库最适合通过 top/tie business 放宽或 prompt 约束改善。

### thrombosis_prediction

Gap 题：36。BIRD 风格 35，schema/linking 1。

主要问题：

- 最大类是明细行 vs 唯一患者。Pontis 频繁 `COUNT(DISTINCT ID)`、`EXISTS`、`INTERSECT`，gold 按 Laboratory/Examination 记录行计数。
- 公式尺度：Q1150 少乘 100。
- 检测字段过度解析：Q1249 `U-PRO`，Q1260 `RF`。
- 输出字段错：Q1212、Q1285。
- 常见 wrong pattern：
  - `COUNT(DISTINCT p.ID)` 应是 `COUNT(*)`
  - `WHERE EXISTS (...)` 应是直接 JOIN 后计数
  - `GROUP BY ID` 改变检查记录行粒度

这个库的主要问题不是图谱，而是 Pontis 的默认业务语义“患者唯一实体”与 BIRD gold 不一致。

### toxicology

Gap 题：20。BIRD 风格 13，schema/linking 7。

主要问题：

- bond 两端输出形状：Q210/Q216/Q236/Q248/Q277/Q285/Q305，Pontis常只返回一端、返回 element、或多返回解释列。
- atom_id / atom_id2 方向 linking：Q257。
- molecule/atom/bond 粒度：Q260/Q310。
- 输出聚合形状：Q306 把元素 group_concat 成一行，gold 是多行 `(molecule_id, element)`。
- 代码值解释：Q280 把 `+/-` 改成 carcinogenic/non-carcinogenic。
- 百分比少乘 100：Q324。

这个库既有 BIRD 输出形状问题，也有真实 schema linking 问题，尤其是 `connected.atom_id` 和 `connected.atom_id2`。 

## 能否通过放宽 Business 继续解决

可安全继续放宽的很少，大约 10 到 15 题：

- 百分比 `0.x` vs `x%`
- top/max/min 并列超集且业务等价
- 少量代码值解释标签等价
- 少量 raw tags vs tag name，是否放宽有争议

不建议放宽：

- `COUNT(DISTINCT entity)` vs `COUNT(row)`：语义不同。
- denominator 改变：语义不同。
- 字段/表/路径选错：语义不同。
- 精确条件变成模糊条件：语义不同。
- 执行错误/timeout：不是 result matching 问题。

因此，继续放宽 business 指标不能解决主差距。主要改进点仍是生成阶段对 BIRD 行粒度、原始字段和最小输出表的服从。 
