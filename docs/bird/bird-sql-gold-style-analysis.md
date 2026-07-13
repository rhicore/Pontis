# BIRD SQL 风格与 Schema Linking 边界

本文记录两件事：

1. 定义 Pontis 分析 BIRD 错题时如何区分“数据集风格”和 “schema linking”，并把近期错题中的典型问题映射到这个体系。
2. 基于 BIRD dev 全量 gold SQL，通过 question 关键词触发统计，总结 BIRD 数据集整体 SQL 风格倾向。

## 一、分类定义与旧错题映射

### Schema Linking

Schema linking 指 question/evidence 中的业务概念应该落到哪个数据库对象，包括表、列、枚举值、join path、字段角色、指标来源和行粒度。

它回答的是：

- 题面中的实体、属性、指标、谓词、时间、地点、枚举值分别对应哪些表列。
- 连接路径应该使用哪些表和 key。
- 字段是 code、name、type、description、status、identifier 还是 measure。
- 表或 join 后的一行代表什么实体或事件。
- 题面中的数量概念是已有指标列，还是需要从明细行重新统计。

因此 schema linking 不只是“找表找列”。当错误来自字段角色、指标血缘或 row grain 判断时，也应归入广义 schema linking。

### BIRD SQL 风格

BIRD SQL 风格指在 schema linking 基本成立后，gold SQL 对 SQL 组织形式和结果表形状的稳定偏好。

它回答的是：

- SELECT 结果表应该输出哪些列、按什么顺序输出。
- 排序字段、过滤字段、join key、计算中间量是否进入 SELECT。
- top-N、极值、rank、percentage、full name、NULL、DISTINCT 等应该采用哪种 BIRD gold 口径。
- SQL 是否符合 BIRD gold 的常见写法，例如单条 SQL、直接等值 join、最小答案表。

如果问题的核心是“正确对象已经找到了，但 SQL 输出形式不符合 BIRD gold”，归入 BIRD SQL 风格。

### 交界类：指标血缘与聚合粒度 Linking

`How many` 相关错误经常处在两类之间。它不是单纯 SQL 写法问题，而是要判断题面数量概念的血缘：

- 如果数据库已有一个字段表示该数量，SQL 应直接返回该字段。
- 如果数据库只有明细行，SQL 应使用 `COUNT`、`SUM`、`AVG` 或 `COUNT(DISTINCT)`。

这个判断依赖字段角色和 row grain，因此本质上属于 schema linking 的扩展；但最终表现通常是 `COUNT/SUM` 与直接 SELECT 字段之间的 SQL 形状差异，所以旧文档里常被记为 `SQL_STYLE / 聚合口径`。

后续建议单独标注为：

```text
METRIC_LINEAGE / AGGREGATION_GRAIN
```

中文可称为：指标血缘与聚合粒度 linking。

### 旧错题问题映射

主要属于 schema linking 的问题：

| 类型 | 例子 | 说明 |
| --- | --- | --- |
| 字段来源选择 | `schools.FundingType` vs `frpm.Charter Funding Type` | 题面 funding 概念应落到哪个表侧，不是 SQL 风格能决定。 |
| code/name/type 角色 | `DOC` vs `DOCType`，`EILCode` vs `EILName`，`SOC` vs `SOCType` | 需要知道题面问代码、名称还是类别描述。 |
| 地理/机构短语落点 | county/city/district/school district | 同一个地名词可能是地点，也可能是机构名称。 |
| grade span 字段 | `Low Grade/High Grade` vs `GSserved/GSoffered` | 题面中的 served/offered/low/high grade 要绑定到对应字段。 |
| admin 槽位 | `AdmFName1/2/3`、`AdmEmail1/2/3` | 题面是否要求多个管理员槽位属于字段角色和实体槽位判断。 |
| table grain / event source | F1 `results`、`lapTimes`、`driverStandings` 等 | 相似指标存在于不同粒度的表中，必须选择正确事件来源。 |

主要属于 BIRD SQL 风格的问题：

| 类型 | 例子 | 说明 |
| --- | --- | --- |
| SELECT 多列/少列 | 题面只要 phone，SQL 多输出 school；题面要求 score，SQL 漏输出 score | BIRD 对答案表列数敏感。 |
| SELECT 列顺序 | 地址、姓名、指标和实体顺序与 gold 不同 | 输出 multiset 比较会受列顺序影响。 |
| 排序指标误输出 | highest/top 题把排序指标也放进 SELECT | BIRD gold 多数只输出被询问答案列。 |
| 报表化输出 | `UNION ALL`、metric/value 行、多段 SQL | BIRD gold 通常是单一答案表。 |
| full name 拼接 | `forename || ' ' || surname` | 很多 BIRD gold 按原始 name 字段分列输出。 |
| join 写法风格 | 连接 key 上 `SUBSTR`、补零、CAST、LOWER/TRIM | BIRD gold 通常使用原始列简单等值 join。 |
| top-N 写法 | 先子查询取 top 再 join，或 join 后再取 top | 两者可能改变集合，BIRD gold 常偏向在最终候选集合上排序取行。 |

指标血缘与聚合粒度 linking：

| 类型 | 例子 | 说明 |
| --- | --- | --- |
| `How many` vs 已有 count 字段 | `NumTstTakr` | 题面问某学校有多少 test takers，gold 可能直接返回 `NumTstTakr`，不是 `COUNT(*)` 或 `SUM(NumTstTakr)`。 |
| 已有平均值/分数 | `AvgScrMath`、`AvgScrWrite`、F1 points/wins/rank | 字段本身可能已经是指标，题面问该指标时不应重新聚合。 |
| 明细行计数 vs 去重实体计数 | `COUNT(driverId)` vs `COUNT(DISTINCT driverId)` | 需要判断题面问事件记录数还是唯一实体数。 |
| row-level ratio vs group-level ratio | 每行已有分子分母，或需要先分组汇总再算比率 | 取决于 question/evidence 和表粒度。 |
| `each/per/by` 是否 GROUP BY | 原始表一行已经对应答案记录时不应自动分组 | 关键词不能直接决定聚合。 |

这个类别的修复方式不应只靠 SQL writer prompt。图谱或调查报告需要明确：

- 表的一行代表什么。
- 列是原始属性还是预计算 measure。
- measure 的统计对象和粒度。
- measure 是否可跨行重新聚合。

## 二、BIRD Dev 关键词风格调查

### 调查口径

调查范围是 `data/bird_dev/dev.json` 的 1534 条 gold SQL。关键词触发只看 question 文本，避免 evidence 里的字段名污染意图统计。例如 `Free Meal Count` 不能被当作题面要求 `COUNT` 的证据。

统计项包括：

- top-level SELECT 表达式数量。
- SELECT 中是否出现 `COUNT/SUM/AVG/MIN/MAX`。
- 是否使用 `GROUP BY`、`ORDER BY`、`LIMIT`、`DISTINCT`、`JOIN`、`LEFT JOIN`、子查询、`IS NOT NULL`、`CAST`、`STRFTIME`、`LIKE`。

### 全局结构

| 特征 | 数量 | 比例 |
| --- | ---: | ---: |
| SELECT 1 个表达式 | 1248 / 1534 | 81.4% |
| SELECT 2 个表达式 | 199 / 1534 | 13.0% |
| SELECT 3 个表达式 | 69 / 1534 | 4.5% |
| SELECT 中使用聚合函数 | 560 / 1534 | 36.5% |
| 使用 COUNT | 430 / 1534 | 28.0% |
| 使用 COUNT(DISTINCT) | 59 / 1534 | 3.8% |
| 使用 SUM | 156 / 1534 | 10.2% |
| 使用 AVG | 40 / 1534 | 2.6% |
| 使用 GROUP BY | 120 / 1534 | 7.8% |
| 使用 ORDER BY | 305 / 1534 | 19.9% |
| 使用 LIMIT | 293 / 1534 | 19.1% |
| 使用 DISTINCT | 179 / 1534 | 11.7% |
| 使用 JOIN | 1140 / 1534 | 74.3% |
| 使用 LEFT JOIN | 1 / 1534 | 0.1% |
| 使用窗口函数 | 5 / 1534 | 0.3% |
| 使用 UNION | 1 / 1534 | 0.1% |
| 使用子查询 | 124 / 1534 | 8.1% |
| 使用 IS NOT NULL | 73 / 1534 | 4.8% |
| 使用 CAST | 152 / 1534 | 9.9% |
| 使用 STRFTIME | 112 / 1534 | 7.3% |
| 使用 LIKE | 42 / 1534 | 2.7% |

结论：BIRD dev gold 的强风格是最小答案表、少 GROUP BY、少窗口函数、少 UNION、几乎不用 LEFT JOIN。大部分复杂性来自 schema linking 和条件选择，而不是 SQL 形状本身。

### Question 关键词矩阵

下表统计 question 中出现关键词时，对应 gold SQL 的结构倾向。多个类别可以重叠。

| 关键词类别 | N | SELECT 1列 | SELECT 多列 | 聚合 | COUNT | SUM | AVG | GROUP BY | ORDER+LIMIT | DISTINCT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `how many` | 317 | 305 (96.2%) | 12 (3.8%) | 301 (95.0%) | 283 (89.3%) | 21 (6.6%) | 1 (0.3%) | 17 (5.4%) | 6 (1.9%) | 3 (0.9%) |
| `number of` | 92 | 75 (81.5%) | 17 (18.5%) | 57 (62.0%) | 41 (44.6%) | 14 (15.2%) | 4 (4.3%) | 16 (17.4%) | 28 (30.4%) | 2 (2.2%) |
| `count` | 23 | 16 (69.6%) | 7 (30.4%) | 13 (56.5%) | 10 (43.5%) | 2 (8.7%) | 1 (4.3%) | 2 (8.7%) | 6 (26.1%) | 2 (8.7%) |
| `total/sum/in all` | 77 | 61 (79.2%) | 16 (20.8%) | 56 (72.7%) | 23 (29.9%) | 29 (37.7%) | 5 (6.5%) | 6 (7.8%) | 8 (10.4%) | 4 (5.2%) |
| `average/mean` | 112 | 92 (82.1%) | 20 (17.9%) | 75 (67.0%) | 32 (28.6%) | 22 (19.6%) | 40 (35.7%) | 14 (12.5%) | 21 (18.8%) | 3 (2.7%) |
| `percent/rate/ratio` | 134 | 122 (91.0%) | 11 (8.2%) | 117 (87.3%) | 94 (70.1%) | 78 (58.2%) | 1 (0.7%) | 3 (2.2%) | 12 (9.0%) | 5 (3.7%) |
| `top/highest/lowest/...` | 306 | 229 (74.8%) | 76 (24.8%) | 57 (18.6%) | 36 (11.8%) | 14 (4.6%) | 2 (0.7%) | 72 (23.5%) | 220 (71.9%) | 15 (4.9%) |
| `rank/ranking` | 14 | 7 (50.0%) | 7 (50.0%) | 3 (21.4%) | 2 (14.3%) | 1 (7.1%) | 0 (0.0%) | 2 (14.3%) | 1 (7.1%) | 1 (7.1%) |
| `list/show/give/provide/find` | 394 | 262 (66.5%) | 132 (33.5%) | 37 (9.4%) | 26 (6.6%) | 15 (3.8%) | 1 (0.3%) | 34 (8.6%) | 77 (19.5%) | 102 (25.9%) |
| `which/what` | 688 | 545 (79.2%) | 142 (20.6%) | 184 (26.7%) | 104 (15.1%) | 92 (13.4%) | 32 (4.7%) | 75 (10.9%) | 188 (27.3%) | 69 (10.0%) |
| `each/for each/per/by` | 126 | 92 (73.0%) | 34 (27.0%) | 62 (49.2%) | 41 (32.5%) | 23 (18.3%) | 7 (5.6%) | 17 (13.5%) | 15 (11.9%) | 14 (11.1%) |
| `distinct/unique/different` | 6 | 3 (50.0%) | 3 (50.0%) | 1 (16.7%) | 1 (16.7%) | 0 (0.0%) | 0 (0.0%) | 1 (16.7%) | 2 (33.3%) | 4 (66.7%) |
| `name/full name` | 256 | 161 (62.9%) | 95 (37.1%) | 14 (5.5%) | 9 (3.5%) | 5 (2.0%) | 0 (0.0%) | 25 (9.8%) | 75 (29.3%) | 39 (15.2%) |
| `url/website/introduction` | 13 | 9 (69.2%) | 4 (30.8%) | 2 (15.4%) | 1 (7.7%) | 1 (7.7%) | 0 (0.0%) | 1 (7.7%) | 3 (23.1%) | 2 (15.4%) |
| `date/year/time/...` | 235 | 173 (73.6%) | 61 (26.0%) | 84 (35.7%) | 54 (23.0%) | 24 (10.2%) | 14 (6.0%) | 18 (7.7%) | 78 (33.2%) | 19 (8.1%) |
| `if there are/any/available` | 33 | 26 (78.8%) | 7 (21.2%) | 13 (39.4%) | 12 (36.4%) | 5 (15.2%) | 0 (0.0%) | 0 (0.0%) | 1 (3.0%) | 6 (18.2%) |
| `country/city/location/...` | 170 | 98 (57.6%) | 72 (42.4%) | 29 (17.1%) | 23 (13.5%) | 8 (4.7%) | 0 (0.0%) | 10 (5.9%) | 44 (25.9%) | 24 (14.1%) |

### 关键词风格结论

#### `how many` 最接近 COUNT，但仍需指标血缘判断

`how many / number of / count` 合并后共有 415 题，其中：

- 使用聚合：355 / 415。
- 未使用聚合：60 / 415。
- 使用 COUNT：320 / 415。
- 使用 SUM：36 / 415。
- SELECT 1 列：380 / 415。
- 使用 GROUP BY：33 / 415。

因此，BIRD dev 中 `how many` 的主流确实是单列 COUNT，但仍有约 14.5% 没有聚合。这些通常不是“数行”，而是用已有 measure 列作为答案或排序依据，例如 `NumTstTakr`、`Enrollment`、`AvgScrMath`、`FRPM Count` 等。

结论：`how many` 可以作为 COUNT 的强提示，但不能覆盖 measure lineage。必须先判断题面数量是否已有列表达。

#### `number of` 比 `how many` 更容易指向已有指标

`how many` 中 95.0% 使用聚合；`number of` 中只有 62.0% 使用聚合，且 30.4% 使用 `ORDER BY + LIMIT`。

这说明 `number of X` 经常出现在 “the highest number of X” 这类描述里。此时 number 是排序指标，不一定是 SELECT 目标，也不一定要求 COUNT。

#### `total/sum` 倾向聚合，但不等于一定 SUM

`total/sum/in all` 类 77 题中，72.7% 使用聚合，但只有 37.7% 使用 `SUM`。原因是 `total` 也可能出现在 “total number of rows/entities” 中，此时 gold 用 `COUNT`。

结论：`total/sum` 是聚合强提示，但聚合函数仍需由目标对象决定：已有金额/数量字段才 `SUM`，实体数量仍 `COUNT`。

#### `average/mean` 不等于一定 AVG

`average/mean` 类 112 题中，67.0% 使用聚合，35.7% 使用 `AVG`。很多题问的是已有 average 字段，例如 average score/rating/speed，而不是要求重新计算平均。

结论：遇到 `average` 先检查是否存在已有 average measure；字段本身是平均值时，BIRD gold 常直接返回或排序该字段。

#### `percent/rate/ratio` 基本是单列计算表达式

`percent/rate/ratio` 类 134 题中：

- SELECT 1 列：91.0%。
- 使用聚合：87.3%。
- 使用 CAST：83.6%。
- 使用 GROUP BY：2.2%。

结论：BIRD dev 中比例题通常返回一个单列数值表达式，常见写法是 `CAST(numerator AS REAL) * 100 / denominator` 或类似形式。比例题很少按组输出报表。

#### top/highest/lowest 主要是 ORDER BY + LIMIT，不自动输出排序指标

`top/highest/lowest/...` 类 306 题中：

- 使用 `ORDER BY + LIMIT`：220 / 306。
- SELECT 1 列：229 / 306。
- 使用聚合：57 / 306。
- 使用 GROUP BY：72 / 306。

结论：top/极值词优先决定候选集合排序，不自动决定 SELECT 输出排序指标。只有题面明确要求 “indicate the amount / include score / list metric” 时，排序指标才应进入 SELECT。

#### `each/by/per` 不是 GROUP BY 的充分条件

`each/for each/per/by` 类 126 题中，只有 17 题使用 `GROUP BY`。很多题里的 each/per/by 是自然语言介词或 row-level 描述，不是分组聚合要求。

结论：`each/by/per` 只能作为 GROUP BY 候选提示，不能直接触发 GROUP BY。要先判断原始行是否已经是一条答案记录。

#### `list/show/give/provide/find` 更容易多列，但仍以最小答案表为主

这类 394 题中：

- SELECT 1 列：66.5%。
- SELECT 多列：33.5%。
- 使用聚合：9.4%。
- 使用 DISTINCT：25.9%。

结论：list/show 类题更可能要求多列或去重列表，但仍然不能额外输出排序字段、过滤字段和解释字段。

#### `name` 和 `location` 类题多列比例更高

`name/full name` 类中 SELECT 多列为 37.1%；`country/city/location/address` 类中 SELECT 多列为 42.4%。这主要来自 full name、地址、经纬度、城市国家组合等多属性答案。

结论：多列输出通常来自题面明确列举的属性集合，而不是 SQL 计算需要。

#### `date/year/time` 类题常用 STRFTIME，但不是唯一口径

`date/year/time` 类 235 题中：

- 使用 `STRFTIME`：62 / 235。
- 使用 `ORDER BY + LIMIT`：78 / 235。
- 使用聚合：84 / 235。

结论：年份筛选常用 `STRFTIME('%Y', col)`；但时间类字段在不同库里可能是文本、秒数、毫秒数或日期字符串，具体处理仍依赖字段事实。

#### `distinct/unique/different` 明确时 DISTINCT 倾向强，但样本少

question 明确出现 distinct/unique/different 的只有 6 题，其中 4 题使用 `DISTINCT` 或 `COUNT(DISTINCT)`。样本少，但方向明确：去重词是 DISTINCT 的强提示。

### 数据库间差异

各数据库仍保持整体最小答案表倾向，但聚合比例有差异：

| 数据库 | 题数 | SELECT 1列 | 聚合 | GROUP BY |
| --- | ---: | ---: | ---: | ---: |
| california_schools | 89 | 59 (66.3%) | 32 (36.0%) | 8 (9.0%) |
| card_games | 191 | 159 (83.2%) | 56 (29.3%) | 14 (7.3%) |
| codebase_community | 186 | 154 (82.8%) | 76 (40.9%) | 12 (6.5%) |
| debit_card_specializing | 64 | 56 (87.5%) | 33 (51.6%) | 18 (28.1%) |
| european_football_2 | 129 | 121 (93.8%) | 39 (30.2%) | 11 (8.5%) |
| financial | 106 | 90 (84.9%) | 50 (47.2%) | 8 (7.5%) |
| formula_1 | 174 | 121 (69.5%) | 45 (25.9%) | 12 (6.9%) |
| student_club | 158 | 118 (74.7%) | 50 (31.6%) | 11 (7.0%) |
| superhero | 129 | 118 (91.5%) | 46 (35.7%) | 4 (3.1%) |
| thrombosis_prediction | 163 | 133 (81.6%) | 80 (49.1%) | 6 (3.7%) |
| toxicology | 145 | 119 (82.1%) | 53 (36.6%) | 16 (11.0%) |

`debit_card_specializing` 的 GROUP BY 比例明显更高，说明部分库的业务问题确实更偏分组统计。Formula 1 的 SELECT 1 列比例较低，主要是姓名、时间、赛道信息等多属性题较多；但 GROUP BY 仍只有 6.9%。

### 对 Pontis 的含义

BIRD dev 的关键词风格可以提供默认偏置，但不能替代 schema linking：

- `how many` 默认向单列 COUNT 靠拢，但遇到已有 measure 列时应优先判断指标血缘。
- `top/highest/lowest` 默认向 `ORDER BY + LIMIT` 靠拢，但 SELECT 仍只输出题面要求的答案列。
- `each/by/per` 不能自动 GROUP BY。
- `average/number/rate` 这些词必须先判断是否已有预计算字段。
- `list/show/name/location` 类题允许多列，但多列必须来自题面列举的答案槽。

所以，BIRD 风格能解决的是 SQL 结果表形状和默认写法；schema linking 仍需图谱提供字段角色、指标来源、行粒度和数据产品语境。
