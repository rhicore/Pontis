# Spider 2.0 Snow 宽表数据库与 Column Group 设计说明

本文档总结 Spider 2.0 Snow 中 7 万列级别数据库的来源、`table_group` 缩减后的剩余问题，以及是否需要引入 `column_group` 作为新的认知压缩单位。

## 0. 宽表到底是什么

宽表不是指“表很多”，而是指“单张表的列很多，并且很多列其实是在表达同一类事实的不同条件、分桶或时间点”。

一个普通关系表通常长这样：

```text
student_id | name | age | school_id
```

这里每一列都比较像一个稳定属性。`age` 是年龄，`school_id` 是学校外键。Agent 看到这些列，通常可以直接理解它们在 SQL 中的作用。

宽表则更像这样：

```text
geo_id | male_55_to_59 | female_55_to_59 | income_50000_59999 | income_60000_74999 | median_rent
```

这些列不是普通实体属性，而是被展开后的统计指标：

```text
male_55_to_59
  measure: population count
  sex: male
  age_bucket: 55_to_59

income_50000_59999
  measure: household count
  income_bucket: 50000_59999
```

也就是说，宽表把一部分“本来可以作为行值或维度值出现的语义”写进了列名。

如果把上面的宽表改成长表，它会更像这样：

```text
geo_id | metric_name       | metric_value
---    | ---               | ---
10001  | male_55_to_59     | 1234
10001  | female_55_to_59   | 1301
10001  | income_50000_59999| 802
10001  | median_rent       | 1800
```

长表的好处是 schema 很小，只需要 `metric_name` 和 `metric_value`。宽表的好处是分析查询通常更直接，很多 BI/仓库数据也会为了性能、兼容性或发布格式把指标摊平成列。

Spider 2.0 Snow 中的 ACS、BLS、COVID 等库大量采用这种宽表形态。因此问题不是“这些库真的有 7 万个独立业务概念”，而是：

```text
同一批统计指标
  × 多个年份
  × 多个地理粒度
  × 多个统计周期
  × 宽表列展开
```

宽表对 Text2SQL 的困难主要有三点：

1. schema 展示困难：Agent 很难一次读完几百个相似指标列。
2. relationship 检测困难：大部分 metric 列不应该参与 join/overlap。
3. 用户问题匹配困难：用户说“income”或“rent”时，需要先定位指标组，再展开到具体列。

因此，对宽表的处理重点不是继续给每一列写 detail，而是建立一个虚拟 refinement layer：

```text
table_group: 处理同构表/年份表重复
column_group: 处理表内指标列展开
column_role: 区分 identifier / metric / category / administrative
```

这个 refinement layer 不替代官方原表。最终 SQL 仍然引用原始表名和原始列名。它只是让 Agent 在检索和规划阶段先看到更合理的语义结构。

## 1. 7 万列是什么情况

Spider 2.0 Snow 的 152 个数据库列数分布非常不均匀。按官方 `resource/databases/*/*/*.json` 中的 `column_names` 统计：

| 指标 | 数值 |
| --- | ---: |
| 数据库数量 | 152 |
| 最小列数 | 4 |
| 中位数列数 | 223 |
| 平均列数 | 3,436 |
| p75 | 1,028 |
| p90 | 3,770 |
| p95 | 7,450 |
| 最大列数 | 71,832 |

超大库集中在少数几个数据库：

| 数据库 | 列数 | 表数 | schema 数 | 主要原因 |
| --- | ---: | ---: | ---: | --- |
| FEC | 71,832 | 486 | 5 | FEC 业务表 + 大量 Census/ACS 宽表 |
| COVID19_USA | 70,858 | 285 | 3 | COVID 时间序列/地区粒度宽表 |
| CENSUS_BUREAU_ACS_1 | 69,170 | 351 | 3 | ACS 宽表 |
| SDOH | 68,863 | 294 | 8 | 社会健康因素 + ACS 类宽表 |
| CENSUS_BUREAU_ACS_2 | 68,434 | 296 | 3 | ACS 宽表 |
| BLS | 23,193 | 143 | 3 | 按年份/季度/行业等重复展开 |
| GOOGLE_DEI | 23,123 | 140 | 3 | 包含 BLS 类统计宽表 |

所以 7 万列不是普遍现象，但也不是只有 FEC 一个。需要特殊处理的主要是前 5 个接近 7 万列的数据库，以及少数 1 万到 2 万列级别的统计/基因/时间序列数据库。

### FEC 为什么有 71,832 列

FEC 并不只是 FEC 竞选财务数据。它包含多个 schema：

| schema | 表数 | 列数 |
| --- | ---: | ---: |
| CENSUS_BUREAU_ACS | 278 | 68,253 |
| FEC | 146 | 2,668 |
| GEO_CENSUS_TRACTS | 57 | 742 |
| FDIC_BANKS | 2 | 151 |
| HUD_ZIPCODE_CROSSWALK | 3 | 18 |

真正造成 7 万列的是 `CENSUS_BUREAU_ACS`，不是 FEC 业务表本身。FEC 业务表规模相对正常，例如：

| 表 | 列数 | 说明 |
| --- | ---: | --- |
| INDIV20 | 21 | 个人捐款记录 |
| CM20 | 15 | 委员会信息 |

ACS 表则是典型宽表。比如 `CENSUS_BUREAU_ACS.CBSA_2018_1YR` 有 252 列，列名包括：

```text
male_55_to_59
father_one_parent_families_with_young_children
commuters_by_public_transportation
median_rent
income_125000_149999
white_pop
gini_index
```

这些列不是关系型建模中的普通属性，而是把统计指标、维度条件和度量值编码进列名：

```text
male_55_to_59
  metric/value: population count
  sex: male
  age_range: 55_to_59

income_125000_149999
  metric/value: household count
  income_bucket: 125000_149999
```

换句话说，它把本来可以放在行里的语义展开成了列。这类结构在分析型数据集、统计数据、数据立方体、时间序列表中很常见，但对 Text2SQL 的 schema reading 很不友好。

### 列数膨胀的乘法结构

FEC/ACS 的列数膨胀来自多个维度相乘：

```text
地理粒度 × 年份 × ACS 周期 × 指标列集合
```

例如：

```text
CBSA_2007_1YR
CBSA_2008_1YR
...
CBSA_2021_1YR

COUNTY_2010_5YR
COUNTY_2011_5YR
...
COUNTY_2020_5YR

CENSUSTRACT_2010_5YR
CENSUSTRACT_2011_5YR
...
```

每张表 200 多列，其中大部分列是同一套统计指标。表名负责表达地理层级、年份、周期；列名负责表达具体指标和子维度。

因此，这 7 万列不是 7 万个互不相关的业务概念，而是高度规则化的宽表展开。

## 2. 为什么表组缩减后还剩很多列

`table_group` 解决的是“同构表重复”的问题。它把一批结构相同、只在年份/分区/周期上不同的表视为同一个逻辑表族。

以 FEC 为例，当前表组结果：

| 项 | 数量 |
| --- | ---: |
| 原始表数 | 486 |
| 原始列数 | 71,832 |
| table_group 数 | 41 |
| 被 table_group 覆盖的表 | 419 |
| standalone table 数 | 67 |
| table_group + standalone 后的 logical table units | 108 |
| 每个 table_group 取一个代表表后的代表列数 | 8,528 |

这说明 `table_group` 很有效：它把 486 张物理表压到 108 个逻辑表单位，把 71,832 个物理列压到 8,528 个代表列。

但 8,528 列仍然很多，原因是 `table_group` 只压缩了“横向重复的表”，没有压缩“表内部的宽指标列”。

### table_group 能解决什么

`table_group` 可以处理这种重复：

```text
CBSA_2007_1YR
CBSA_2008_1YR
CBSA_2009_1YR
...
CBSA_2021_1YR
```

这些表拥有高度相似的列集合。对 Agent 来说，不需要逐张表理解，只需要理解：

```text
CBSA_YYYY_1YR
  grain: CBSA geography + year + 1-year ACS estimate
  columns: geo_id + ACS metric columns
```

### table_group 不能解决什么

`table_group` 不能处理同一张代表表里的 200 多个指标列：

```text
CBSA_2018_1YR
  male_55_to_59
  female_35_to_39
  income_50000_59999
  median_rent
  commuters_by_public_transportation
  ...
```

这些列虽然属于同一张表，但它们不是同一个物理重复模式。它们是同一个统计宽表内部的指标展开。

因此，表组后的剩余问题本质上是：

```text
table_group 解决 table-level repetition
column_group 需要解决 column-level semantic expansion
```

### 为什么不能继续对 8,528 代表列做两两 overlap

即使只看代表列，8,528 列两两比较也有约 3,600 万列对：

```text
8528 * 8527 / 2 ≈ 36,359,128
```

这仍然不适合直接做 value overlap。更重要的是，其中大量列根本不应该参与 overlap。例如：

```text
male_55_to_59
median_rent
income_50000_59999
commuters_by_public_transportation
```

这些是 metric/measure 列，不是 join key，也不是 disambiguation 的主要候选。它们进入 overlap 只会制造噪声和成本。

一个粗略脚本规则统计显示，FEC 代表列 8,528 个中：

| 类型 | 估计数量 | 说明 |
| --- | ---: | --- |
| key/category-like columns | 约 674 | `id`, `code`, `zip`, `state`, `county`, `geo_id`, `fips`, `cmte_id`, `cand_id` 等 |
| metric-noise columns | 约 6,192 | `income_*`, `population`, `median_*`, `percent_*`, `male_*`, `female_*`, `rent_*`, `housing_*` 等 |

所以正确方向不是“让 8,528 列继续两两比较”，而是先做列角色识别和列组压缩。

## 3. 是否需要 Column Group

结论：需要，但不应该把 `column_group` 做成所有数据库通用的强制实体。它更适合用作宽表/统计表/数据立方体的 refinement entity。

### Column Group 适合解决什么

`column_group` 适合表达同一张表或同一个 table_group 代表表内部的一组语义相似列。例如 ACS 宽表中可以形成：

| column_group | 示例列 | 语义 |
| --- | --- | --- |
| population_by_age_sex | `male_55_to_59`, `female_35_to_39`, `male_15_to_17` | 按年龄/性别展开的人口统计 |
| income_bucket | `income_50000_59999`, `income_125000_149999` | 收入分桶 |
| housing_rent_burden | `rent_20_to_25_percent`, `rent_over_50_percent`, `median_rent` | 租金与住房负担 |
| commuting | `commute_less_10_mins`, `commuters_by_public_transportation`, `worked_at_home` | 通勤方式/时间 |
| race_ethnicity | `white_pop`, `black_pop`, `asian_pop`, `hispanic_pop` | 种族/族裔统计 |
| education | `high_school_including_ged`, `bachelors_degree`, `graduate_professional_degree` | 教育程度 |
| employment | `employed_*`, `unemployed_pop`, `civilian_labor_force` | 就业统计 |
| geography_identifiers | `geo_id`, `state`, `county`, `tract`, `zip_code`, `fips` | 地理连接键 |

这类列组可以显著降低 Agent 的 schema 阅读负担。Agent 不需要先读 252 个列名，而是先读：

```text
This ACS table contains geography identifiers plus metric groups:
population_by_age_sex, income_bucket, housing_rent_burden, commuting, race_ethnicity, education, employment.
```

当问题具体问到收入、通勤、年龄、住房时，再展开对应 column_group 下的具体列。

### Column Group 不应该替代表和列

`column_group` 不应该物理替代 column 节点。原因：

1. 最终 SQL 仍然必须引用官方原始列名。
2. 一个问题可能需要精确列，例如 `median_rent`，不能只停留在 `housing_rent_burden`。
3. 某些列可能跨多个语义组，例如 `hispanic_male_45_54` 同时涉及 ethnicity、sex、age。

所以更合理的结构是：

```text
table_group
  --HAS_REPRESENTATIVE_TABLE--> table

table
  --HAS_COLUMN--> column

column_group
  --GROUPS_COLUMN--> column
  --APPLIES_TO_TABLE_GROUP--> table_group
  --APPLIES_TO_TABLE--> table
```

`column_group` 是一个检索和认知压缩层，不是替换层。

### Column Group 应该存什么元数据

建议最小元数据：

| 字段 | 说明 |
| --- | --- |
| name | 稳定名称，例如 `population_by_age_sex` |
| scope | `table` 或 `table_group` |
| role | `identifier`, `metric`, `dimension_encoded_metric`, `category`, `administrative` |
| detail | AI/explorer 写的自然语言说明 |
| pattern | 生成这个组的规则或列名模式，例如 `male_*`, `female_*`, `income_*` |
| column_count | 组内列数量 |
| sample_columns | 少量示例列名 |
| dimensions | 如果可解析，记录 `sex`, `age_range`, `income_bucket`, `race`, `period` 等 |
| sql_usage_hint | 生成 SQL 时如何选择具体列 |

注意：不要在 `column_group` 元数据里重复存表名/schema 名。如果它已经通过图关系连接到 table 或 table_group，就由图关系表达归属。

### Column Group 可以用纯脚本生成吗

可以，但要分层。

第一层可以纯脚本做，适合 ACS/BLS/COVID 这种规则明显的宽表：

```text
column name pattern -> column role -> column group
```

例如：

| 规则 | group |
| --- | --- |
| `male_*`, `female_*`, `*_age_*` | population_by_age_sex |
| `income_*`, `median_income`, `income_per_capita` | income |
| `rent_*`, `median_rent`, `housing_*` | housing |
| `commute_*`, `commuters_*`, `worked_at_home` | commuting |
| `white_*`, `black_*`, `asian_*`, `hispanic_*` | race_ethnicity |
| `geo_id`, `state`, `county`, `tract`, `zip`, `fips` | geography_identifiers |

第二层需要 explorer/Agent 修正，适合：

1. 名称不规则的业务表。
2. 生物医学/基因类 schema。
3. 表内列语义不能靠 token 判断的库。
4. 需要写高质量 detail 的 column_group。

因此，`column_group` 的生成方式应该是：

```text
extractor: 生成候选 column_group 和粗粒度角色
explorer: 审核/合并/重命名/写 detail
```

### Column Group 和 overlap 的关系

有了 `column_group` 后，overlap 不应该再对所有列做。

推荐策略：

1. 对 `identifier` / `category` column_group 内的列做 overlap。
2. 对 `metric` / `dimension_encoded_metric` column_group 默认不做 value overlap。
3. 对 ACS 这类宽表，只在 geography identifier 组中找连接关系，例如：

```text
geo_id
state
county
tract
zip_code
fips
```

4. metric 列的选择交给 column_group 检索和具体问题匹配，而不是 overlap。

这样可以把 FEC 这种库从：

```text
71,832 physical columns
```

压缩成：

```text
108 logical table units
+ 若干 column groups
+ 少量 identifier columns for relationship detection
```

## 4. 建议方案

我建议引入 `column_group`，但把它定位为 schema refinement，而不是基础 schema extractor 的必选实体。

推荐工作流：

```text
1. spider2_snow_schema extractor
   从官方 JSON/DDL/CSV 建基础 db/schema/table/column 图。

2. db_table_group extractor
   识别年份表、分区表、同构表组。

3. wide_table_refinement extractor
   识别宽表 family。
   标记代表表。
   给列打 role: identifier / metric / category / administrative。
   生成初始 column_group。

4. column_group explorer
   对大库或宽表库审核 column_group。
   合并过细的组。
   给 column_group 写 detail。

5. relationship / overlap extractor
   只对 identifier/category 相关列做 value overlap。
   默认跳过 metric column_group。
```

这个方案比直接把 7 万列全部写 detail 或全部做 overlap 更合理，也更符合 Spider 2.0 Snow 这种真实企业宽表环境。

## 5. 当前判断

1. 7 万列主要来自统计宽表的规则化展开，不是 7 万个独立业务概念。
2. `table_group` 已经有效压缩物理重复表，但不能压缩表内的宽指标列。
3. 需要 `column_group`，尤其是对 ACS/BLS/COVID/SDOH 这种宽表型数据库。
4. `column_group` 应该作为虚拟 refinement 层，不应该替代原始 column。
5. 后续 overlap/relationship 逻辑应该基于 column role 过滤，只比较 identifier/category 列，默认跳过 metric 列。
