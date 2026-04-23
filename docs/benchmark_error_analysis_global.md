# BIRD Benchmark 全量错误归因分析

## 总览

- 总测试：1534 题
- 错误：559 题
- 准确率：63.6%

## 各数据库表现

| 数据库 | 错误 | 总数 | 准确率 |
|---|---|---|---|
| california_schools | 43 | 89 | 51.7% |
| card_games | 80 | 191 | 58.1% |
| codebase_community | 60 | 186 | 67.7% |
| debit_card_specializing | 24 | 64 | 62.5% |
| european_football_2 | 34 | 129 | 73.6% |
| financial | 35 | 106 | 67.0% |
| formula_1 | 86 | 174 | 50.6% |
| student_club | 44 | 158 | 72.2% |
| superhero | 19 | 129 | 85.3% |
| thrombosis_prediction | 81 | 163 | 50.3% |
| toxicology | 53 | 145 | 63.4% |

## 错误分类统计

| 类别 | 数量 | 占比 | 说明 |
|---|---|---|---|
| WHERE 条件差异 | 111 | 19.9% | condition |
| JOIN 路径差异 | 100 | 17.9% | join_path |
| 列选择偏差 | 95 | 17.0% | column_selection |
| 问题理解偏差 | 89 | 15.9% | question_understanding |
| 输出格式差异 | 75 | 13.4% | output_format |
| 聚合逻辑偏差 | 59 | 10.6% | aggregation |
| 百分比计算偏差 | 29 | 5.2% | percentage |
| 未分类 | 1 | 0.2% | unknown |

## 各类别详细分析

### WHERE 条件差异（111 题，可修复 109 题）

典型示例：

- **Q18** [california_schools] 预测SQL使用City='Fresno'，而标准SQL使用County Name='Fresno'，导致条件差异。
- **Q25** [california_schools] 预测SQL错误地将条件限制为单个学校的AvgScrMath>400，而标准SQL要求按学校分组后计算平均分大于400，且未正确关联Funding Type表。
- **Q26** [california_schools] 预测SQL使用了错误的列名'FRPM Count (Ages 5-17)'和'EILName = 'High School''，而标准SQL使用'Free Meal Count (Ages 5-17)'和'School Type = 'High Schools (Public)''，导致条件不匹配。
- **Q28** [california_schools] 预测SQL额外添加了IS NOT NULL条件过滤空值，而标准SQL未显式排除空值，可能导致行数差异
- **Q33** [california_schools] 预测SQL缺少对Website非空的过滤条件（IS NOT NULL），导致可能包含空值网站地址。

### JOIN 路径差异（100 题，可修复 94 题）

典型示例：

- **Q19** [california_schools] 预测SQL使用s.School = sc.sname进行JOIN，但标准SQL使用T1.CDSCode = T2.cds，JOIN条件错误导致无法正确匹配学校与成绩。
- **Q43** [california_schools] 预测SQL未通过JOIN连接schools表获取county信息，而是错误地使用了satscores表中的cname列，导致无法获取正确的county字段。
- **Q80** [california_schools] 预测SQL未使用frpm表进行JOIN，直接查询schools表，缺少与frpm表的关联，导致无法获取正确的School Type字段。
- **Q359** [card_games] 预测SQL通过JOIN sets表并按releaseDate排序取最新发行版本来确定原始类型，而标准SQL直接查询originalType字段，两者获取原始类型的逻辑不同。
- **Q360** [card_games] 预测SQL通过JOIN连接cards、sets和set_translations表，而标准SQL使用子查询通过id关联，两者路径不同但语义等价

### 列选择偏差（95 题，可修复 94 题）

典型示例：

- **Q15** [california_schools] 预测SQL选择了dname列，而标准SQL选择了District列，两者列名不同但可能指向相同含义，属于列选择错误。
- **Q10** [california_schools] 预测SQL仅选择了FRPM计数字段，但缺少FROM子句、JOIN条件和排序/限制逻辑，无法正确关联到SAT最高分的学校。
- **Q71** [california_schools] 预测SQL只选择了NCESDist列，但标准SQL需要从frpm表获取District Code，且缺少JOIN和WHERE条件。
- **Q340** [card_games] 预测SQL选择了name列，而标准SQL选择了id列，属于选错列。
- **Q81** [california_schools] 预测SQL使用了schools表中的School和City，但标准SQL要求从frpm表获取Low Grade和School Name，且预测SQL通过CASE解析GSoffered字段来获取最低年级，与标准SQL直接选取Low Grade字段不同，导致列选择错误。

### 问题理解偏差（89 题，可修复 75 题）

典型示例：

- **Q16** [california_schools] 预测SQL完全忽略了问题中的过滤条件（合并状态、测试人数小于100、县名Lake），直接返回总行数，属于对问题语义的严重理解偏差。
- **Q50** [california_schools] 预测SQL添加了WHERE s.rtype = 'S'条件，而标准SQL没有此条件，导致可能过滤掉部分数据，属于对问题语义的额外理解偏差。
- **Q83** [california_schools] 预测SQL完全忽略了问题中的多个关键条件（Magnet=1、GSoffered='K-8'、NSLP Provision Status='Multiple Provision Types'），仅简单统计了所有城市的学校数量，导致语义理解严重偏差。
- **Q84** [california_schools] 预测SQL将问题理解为统计所有管理员名字（包括AdmFName1/2/3）中最常见的两个名字及其所属学区，而标准SQL仅统计AdmFName1中最常见的两个名字，导致语义理解偏差。
- **Q342** [card_games] 问题要求'cost more converted mana for the face'，提示指最大面转换法术力费用，但标准SQL用ORDER BY LIMIT 1取最小值而非最大值，存在语义理解偏差；预测SQL正确使用了MAX子查询获取最大值。

### 输出格式差异（75 题，可修复 75 题）

典型示例：

- **Q23** [california_schools] 预测SQL将地址拼接为完整街道地址（含城市、州、邮编），而标准SQL仅输出街道字段，输出格式不一致。
- **Q17** [california_schools] 预测SQL缺少RANK()窗口函数，未按题目要求对Writing平均分进行排名，仅按分数降序排序输出数据。
- **Q32** [california_schools] 预测SQL多选了School列，而标准SQL只输出计算比率，导致输出列数不同。
- **Q36** [california_schools] 标准SQL返回多列（每名管理员的名和姓分开），而预测SQL将管理员姓名拼接为单列并用UNION输出多行，格式不一致。
- **Q46** [california_schools] 预测SQL返回了学校名称和 enrollment 两列，而标准SQL只返回学校名称一列，且未限制返回行数（标准SQL用LIMIT 1只返回一行，预测SQL未加LIMIT导致返回多行）。

### 聚合逻辑偏差（59 题，可修复 56 题）

典型示例：

- **Q354** [card_games] 预测SQL使用了COUNT(DISTINCT type)去重计数，而标准SQL使用COUNT(type)直接计数，导致聚合方式不同。
- **Q383** [card_games] 预测SQL使用了COUNT(DISTINCT c.uuid)而标准SQL使用COUNT(T1.id)，两者在数据唯一性上可能产生差异，但问题未明确要求去重，且标准SQL未去重，因此聚合方式不同。
- **Q458** [card_games] 标准SQL统计所有行数（COUNT），预测SQL统计不同艺术家数（COUNT DISTINCT artist），导致聚合粒度不同
- **Q499** [card_games] 预测SQL使用了COUNT(*)而非COUNT(DISTINCT translation)，未对翻译进行去重，可能导致计数偏大
- **Q571** [codebase_community] 标准SQL使用COUNT(DISTINCT T1.Id)对votes去重计数，而预测SQL直接COUNT(*)计数，导致聚合方式不同

### 百分比计算偏差（29 题，可修复 29 题）

典型示例：

- **Q24** [california_schools] 预测SQL直接使用了预计算的百分比字段'Percent (%) Eligible Free (K-12)'，而标准SQL要求根据提示手动计算Free Meal Count (K-12) / Enrollment (K-12)得到百分比，两者计算方式不同。
- **Q352** [card_games] 预测SQL使用COUNT(DISTINCT uuid)计算分子和分母，而标准SQL使用COUNT(id)和SUM(CASE WHEN)计算，导致百分比计算维度不同，且预测SQL未JOIN cards表，分母可能不准确。
- **Q371** [card_games] 预测SQL使用了COUNT(DISTINCT c.id)去重计数，而标准SQL使用COUNT(T1.id)未去重，导致分子分母计算方式不同，百分比结果可能偏差
- **Q403** [card_games] 预测SQL的百分比分母使用了cards表的总行数，而标准SQL的分母是foreign_data表的总行数，导致百分比计算维度不同。
- **Q417** [card_games] 预测SQL将Japanese条件放在WHERE中过滤，导致分母只统计Japanese翻译的集合，而标准SQL分母是所有expansion集合，百分比计算维度不同

### 未分类（1 题，可修复 0 题）

典型示例：

- **Q1218** [thrombosis_prediction] {"category":"3","reason":"预测SQL计算了女性患者中UA>6.5的比例，但标准SQL要求计算女性患者中UA超出正常范围的比例，而正常范围定义依赖于性别，预测SQL未考虑男性U

## 成功案例效率分析

抽样 55 条正确案例：

- 平均工具调用：20.7 次
- 平均耗时：117.3s
- 调用次数合理：6/55

| 数据库 | 平均调用 | 平均耗时 | 合理率 | 建议 |
|---|---|---|---|---|
| california_schools | 23.4 | 123.7s | 0/5 | 合并多次分页的glob调用为一次全量获取，并减少重复的query验证，将工具调用 |
| card_games | 19.8 | 196.3s | 0/5 | 减少重复的glob和query调用，直接通过一次JOIN查询获取所需数据，避免多 |
| codebase_community | 13.6 | 87.9s | 1/5 | 前4次glob调用可合并为一次直接定位表结构，且最终SQL已包含所有信息，无需中 |
| debit_card_specializing | 28.4 | 86.3s | 0/5 | 减少重复的schema探索和中间验证查询，直接通过一次JOIN查询获取结果。 |
| european_football_2 | 10.8 | 154.5s | 3/5 | 无需改进，调用次数合理且无冗余，仅需注意SQL语句完整性即可。 |
| financial | 23.0 | 79.6s | 0/5 | 应合并多次glob和read调用，避免重复查询表结构，并直接使用最终SQL而非多 |
| formula_1 | 27.2 | 192.6s | 0/5 | 应直接使用一次SQL查询完成，避免大量重复的glob、read和中间验证查询。 |
| student_club | 21.0 | 135.0s | 0/5 | 应避免多次重复查询相同数据，先通过少量查询确认表结构和数据关系，再直接写出最终S |
| superhero | 16.8 | 60.1s | 0/5 | 应直接使用SQL查询获取最大属性值的种族，避免大量不必要的glob探索和重复查询 |
| thrombosis_prediction | 27.8 | 109.1s | 0/5 | 应合并多次重复的query调用，先通过一次查询获取所有必要字段，避免反复尝试不同 |
| toxicology | 16.4 | 64.8s | 2/5 | 减少重复的元数据查询和无效的试探性SQL，直接基于表结构写出最终SQL。 |

## 调整建议

### WHERE 条件差异（111 题，可修复 109）
- 预期可修复 109 题，准确率提升约 7.1%

### JOIN 路径差异（100 题，可修复 94）
- 预期可修复 94 题，准确率提升约 6.1%

### 列选择偏差（95 题，可修复 94）
- 预期可修复 94 题，准确率提升约 6.1%

### 问题理解偏差（89 题，可修复 75）
- 预期可修复 75 题，准确率提升约 4.9%

### 输出格式差异（75 题，可修复 75）
- 预期可修复 75 题，准确率提升约 4.9%

### 聚合逻辑偏差（59 题，可修复 56）
- 预期可修复 56 题，准确率提升约 3.7%

### 百分比计算偏差（29 题，可修复 29）
- 预期可修复 29 题，准确率提升约 1.9%
