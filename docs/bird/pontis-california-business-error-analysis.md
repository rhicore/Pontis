# Pontis California Business 错题分类

来源：`20260629_161055_bird_dev_california_hardguard_leftjoin_rerun`

- 数据库：`california_schools`
- Business accuracy：57 / 89 = 64.04%
- Business 下仍错误：32 题
- 错题 QID：4, 10, 15, 16, 17, 19, 21, 23, 24, 25, 26, 27, 28, 33, 36, 37, 41, 43, 44, 49, 52, 53, 54, 63, 65, 70, 71, 72, 81, 83, 87, 88

## 分类总览

| 类别 | 数量 | QID |
| --- | ---: | --- |
| 表/字段/取值 linking 错 | 13 | 4, 10, 19, 21, 24, 25, 26, 28, 41, 43, 65, 71, 81 |
| NULL 或候选行过滤错 | 8 | 15, 23, 27, 33, 37, 44, 52, 88 |
| 聚合或行粒度错 | 4 | 53, 70, 72, 83 |
| 输出形状仍未被 business 放宽 | 3 | 17, 36, 87 |
| BIRD gold 口径窄或题面/gold 冲突 | 2 | 16, 54 |
| 混合问题 | 2 | 49, 63 |

## 1. 表/字段/取值 Linking 错

这些题的主要问题是选错表、错字段、错连接方式、错值解释或错字段表示。即使 business 放宽列顺序、多余列，也无法补救。

- Q4：题面 evidence 明确 charter schools 在 `frpm` 中，gold 用 `frpm.Charter Funding Type` 和 `frpm.Charter School (Y/N)=1`。Pontis 改用 `schools.FundingType`，没有加 `frpm` 的 charter 标志，还过滤 `Phone IS NOT NULL`。
- Q10：gold 直接用 `satscores.cds = frpm.CDSCode`。Pontis 通过 school name 间接回连，最高阅读分学校链接错，返回 69 而不是 136。
- Q19：Pontis 给 `satscores.cds` 手动补前导 0，导致 CDSCode 匹配到错误学校。
- Q21：题面 “In Los Angeles” 在 gold 中落到 `frpm.County Name='Los Angeles'`。Pontis 当成 `schools.City='Los Angeles'`，计数 75 而不是 249。
- Q24：gold 输出 `frpm.School Name`，Pontis 输出 `schools.School` 并额外加 `School IS NOT NULL`，候选集合少。
- Q25：gold 用 `frpm.District Name LIKE 'Riverside%'`，Pontis 用 `schools.County='Riverside'`；gold 还需要按学校和 funding type 分组后判断 average math。
- Q26：gold 用 `frpm.Free Meal Count (Ages 5-17)` 和 `frpm.School Type='High Schools (Public)'`。Pontis 用了 `FRPM Count` 和 `schools.EILName='High School'`，多出错误学校。
- Q28：gold 输出 `schools.DOC` 代码，Pontis 输出 `DOCType` 文本；行数相同但值表示不同。
- Q41：gold 先过滤 `Virtual='F'` 再在虚拟学校集合内按 county rank。Pontis 先对所有学校排名，再筛 virtual，排名范围错。
- Q43：gold join `schools` 输出 `County`，且只过滤 `AvgScrMath IS NOT NULL`。Pontis 用 `satscores.cname`，并强制三科都非空，最低候选换了。
- Q65：gold 在 `Charter=1` 范围内算 locally funded / non-locally-funded。Pontis 用 `FundingType IS NOT NULL` 全体作范围，分母也错。
- Q71：gold 用 `frpm.District Code`。Pontis 从 `schools.CDSCode` 截 district code；覆盖范围不同，多出结果。
- Q81：gold 用 `frpm.Low Grade` 和 `frpm.School Name`，按 `schools.Latitude` 排序。Pontis 从 `schools.GSoffered` 截最低年级，并过滤 `Latitude/School` 非空，top row 换了。

## 2. NULL 或候选行过滤错

这些题的核心是 NULL 行是否属于候选集合。Pontis 经常把图谱里的“NULL 代表某类行”推成了默认过滤；但 BIRD gold 有时保留 NULL 汇总行，有时又明确要求非空。

- Q15：不是简单“过滤 NULL”，而是反向强制 `(School IS NULL OR School='')`，只看 district-level 行。gold 是所有 active joined rows 排序后输出 district。
- Q23：Pontis 多加 `s.School IS NOT NULL`、`s.Street IS NOT NULL`，少了 gold 保留的 3 行。
- Q27：gold 用 `LEFT JOIN` 保留无 SAT 或写作分为空的学校。Pontis 用 inner join，并加 `AvgScrWrite IS NOT NULL`，候选从 8574 行缩到 454 行。
- Q33：gold 明确 `Website IS NOT NULL`，Pontis 没加，返回了一个 website 为 NULL 的学校。这里是少了 gold 要的非空过滤。
- Q37：gold 按 SQLite 自然排序，NULL excellence rate 排在 ASC 前面。Pontis 加 `NumTstTakr > 0` 和 `NumGE1500 IS NOT NULL`，换掉最低候选。
- Q44：Pontis 加 `School IS NOT NULL`，过滤掉最高 `NumGE1500` 的 NULL school 汇总行，top row 从 Los Angeles 换到 Lowell High。
- Q52：Pontis 加 `School IS NOT NULL`，Lakeport 满足条件从 2 行变 1 行。
- Q88：Pontis 加 `School IS NOT NULL AND School != ''`，过滤掉最高 `NumGE1500` 的 NULL school 汇总行，top row 换成 Lowell High。

## 3. 聚合或行粒度错

这些题不是输出列顺序问题，而是题目要明细行还是总数、按组还是不按组、已有指标还是重新聚合的问题。

- Q53：题面问 “How many test takers are there at the school/s ...”，gold 返回每个学校的 `NumTstTakr` 列表。Pontis 做了 `SUM(NumTstTakr)`，变成一行总数。
- Q70：题面问 active and closed District Community Day Schools 总数，gold 返回一行 `COUNT(School)`。Pontis 按 `StatusType` 分组返回 Active/Closed 两行。
- Q72：gold 返回两条 `Enrollment (Ages 5-17)` 记录。Pontis 对两条记录求和，返回一行 375。
- Q83：gold 先过滤 magnet + `GSoffered='K-8'` + Multiple Provision Types，再按 city count。Pontis 用 `GSserved`，并做条件聚合返回所有 K-8 city。

## 4. 输出形状仍未被 Business 放宽

这些题的答案事实接近或包含 gold，但当前 business evaluator 还不能识别这种等价。

- Q17：Pontis 多输出 `school_name`，并且列顺序不同。当前 business 能处理“多列”或“列重排”，但不能同时处理“多列 + 重排”。按执行内容看，去掉 school name 并重排列后与 gold 一致。
- Q36：题面说 full names，Pontis 输出拼接后的 `Michelle King`。gold 输出 3 个管理员槽位的 first/last 分列。当前 business 不接受拼接/拆列等价。
- Q87：Pontis 把 `AdmEmail1`、`AdmEmail2` 用 UNION 拆成两行。gold 是同一行两列。业务上 email 值对，但当前 business 不接受行列转置。

## 5. BIRD Gold 口径窄或题面/Gold 冲突

这些题不适合简单归为 Pontis 业务错误。

- Q16：题面写 “merged Alameda”，但 gold 用 `County='Lake'`。Pontis 按 Alameda 做，business 判错。
- Q54：题面问 Avetik Atoian administration，Pontis 查 Adm1 或 Adm2；gold 只查 Adm1。Pontis 返回更多自然匹配行，gold 口径更窄。

## 6. 混合问题

这些题包含多类问题，不能只归到一个根因。

- Q49：gold 和 Pontis 都有 `School IS NOT NULL`，所以不是 NULL 过滤主因。Pontis 少输出 `County`，并且 county 选择和去重口径与 gold 不完全一致。
- Q63：一部分是 NULL 过滤：Pontis 加 `AdmFName1 IS NOT NULL`，少了 gold 保留的一行 NULL 管理员记录。另一部分是输出形状：Pontis 拼接 full name，gold 要 first/last 分列，并保留 `School, City`。

## 对 Guard 的直接含义

- 空值过滤提醒主要覆盖：Q23, Q27, Q37, Q44, Q52, Q88，以及 Q63 的一部分。
- 结果形状 soft guard 主要覆盖：Q53, Q70, Q72, Q83，可能覆盖 Q49/Q63 的部分问题。
- business evaluator 若继续放宽，可以考虑组合列投影+重排、简单 full name 拼接拆分、两列 email 与单列 UNION 的等价；但这些属于评测设计，不应写成 SQL 生成 prompt 的题目级补丁。
