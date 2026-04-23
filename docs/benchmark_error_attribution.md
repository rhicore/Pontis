# BIRD Benchmark 错误归因与改进分析

## 概况

11 个数据库、1534 条 text-to-sql 测试，其中 559 条错误，准确率 63.6%。错误归因为 7 个类别，每类跨 10-11 个数据库，说明这些是 agent 的系统性缺陷而非某个库的特殊问题。

### 各数据库表现

| 数据库 | 准确率 | 错误数 | 错误特征 |
|---|---|---|---|
| superhero | 85.3% | 19 | 整体良好，少量条件/格式问题 |
| european_football_2 | 73.6% | 34 | 问题理解偏差较多（足球术语） |
| student_club | 72.2% | 44 | 输出格式问题突出（16 题） |
| codebase_community | 67.7% | 60 | WHERE 条件 + JOIN 路径双重问题 |
| financial | 67.0% | 35 | 中等水平，各类错误均匀分布 |
| toxicology | 63.4% | 58 | 百分比计算偏差多（6 题），桥接表理解不足 |
| debit_card_specializing | 62.5% | 24 | 聚合偏差较多（5 题） |
| card_games | 58.1% | 80 | 列选择偏差最多（22 题），卡牌术语难理解 |
| california_schools | 51.7% | 43 | WHERE 条件偏差最多（22 题），地理层级混淆 |
| formula_1 | 50.6% | 86 | JOIN 路径（20 题）+ 列选择（18 题）+ 问题理解（17 题），三重困难 |
| thrombosis_prediction | 50.3% | 81 | WHERE 条件（25 题）+ 聚合偏差（20 题），医学统计口径复杂 |

### 错误分类总览

| 类别 | 数量 | 占比 | 可修复 | 跨数据库数 |
|---|---|---|---|---|
| WHERE 条件差异 | 111 | 19.9% | 109 | 11 |
| JOIN 路径差异 | 100 | 17.9% | 94 | 10 |
| 列选择偏差 | 95 | 17.0% | 94 | 11 |
| 问题理解偏差 | 89 | 15.9% | 75 | 11 |
| 输出格式差异 | 75 | 13.4% | 75 | 10 |
| 聚合逻辑偏差 | 59 | 10.6% | 56 | 10 |
| 百分比计算偏差 | 29 | 5.2% | 29 | 11 |

---

## 一、WHERE 条件差异（111 题，19.9%）

### 错误模式

这是最大的错误来源，覆盖全部 11 个数据库。主要表现为：

1. **条件列选错** — City 和 County 混淆、AvgScrMath 和 AvgScrRead 混淆。agent 根据语义猜测列名而不是查 evidence 或 schema
2. **条件多余** — 自作主张添加 `IS NOT NULL`、`> 0`、`rtype = 'S'` 等过滤，golden SQL 不加这些
3. **条件缺失** — 遗漏问题中的某个限定条件（如"合并状态"、"测试人数小于100"）
4. **AND/OR 逻辑搞反** — 把"同时满足"写成"满足任一"

### 重灾区

- thrombosis_prediction（25 题）— 医学指标的正常范围、性别条件、阈值判断复杂
- california_schools（22 题）— 地理层级混淆（City vs County）、学区 vs 学校
- codebase_community（14 题）— 标签过滤、时间范围

### 典型案例

**Q18** [california_schools] 问 "schools in Fresno County"，agent 用 `City='Fresno'`，golden 用 `County Name='Fresno'`。Fresno 是城市名也是县名，语义歧义。

**Q28** [california_schools] agent 额外加 `IS NOT NULL`，golden 不加。这类"安全过滤"导致行数变少。

**thrombosis_prediction 多题** — 医学指标 UA、HCT 等有性别相关的正常范围，agent 经常用固定阈值而非按性别区分。

### 根因分析

本质上是 agent **没有充分阅读 schema 和 evidence 中的列含义**就急着构造 SQL。BIRD 的 evidence 通常会明确给出正确的列名和条件值，但 agent 有时忽略 evidence 直接根据问题中的自然语言猜列名。

### 改进方向

- **短期（prompt）**：在 `_benchmark.py` 中强调"严格按 evidence 和问题原话构造 WHERE 条件，不要自己添加 evidence 中没有出现的过滤条件"
- **中期（工具使用）**：引导 agent 在构造 WHERE 前先用 `query` 工具执行 `SELECT DISTINCT <疑似列> LIMIT 5` 验证列值是否匹配问题中的条件值
- **长期（schema 语义）**：在提取阶段为列生成更丰富的语义描述（如 "City 和 County 是不同的地理层级"），注入 agent prompt

---

## 二、JOIN 路径差异（100 题，17.9%）

### 错误模式

1. **跳过桥接表** — 多对多关系需要中间表，agent 直接 JOIN 两端表导致笛卡尔积或粒度不匹配
2. **JOIN 粒度松散** — 用 `molecule_id` 做"宽 JOIN"（同属一个分子），而非通过外键做"精确 JOIN"（真正关联）
3. **JOIN 类型错误** — LEFT JOIN vs INNER JOIN 选择不当，丢失或增加行

### 重灾区

- formula_1（20 题）— 表多、关联路径长（results → races → circuits、drivers → standings → races）
- codebase_community（17 题）— 多对多标签关系需要中间表
- card_games（16 题）— cards ↔ sets ↔ translations 多层关联
- toxicology（15 题）— connected 桥接表语义理解不足

### 典型案例

**Q338** [toxicology] 问 "atom ID of double bonded carbon in TR012"：
- Golden: `atom JOIN molecule JOIN bond JOIN connected ON atom_id = connected.atom_id WHERE bond_type = '='` — 通过 connected 找真正参与双键的碳原子
- Predicted: `atom JOIN connected JOIN bond WHERE molecule_id = 'TR012' AND bond_type = '='` — JOIN 条件不够精确，返回了不参与双键的碳原子

根因是 connected 表是 atom 和 bond 之间的桥接表，描述"哪个原子真正参与哪个键"。agent 倾向于跳过它，直接通过 molecule_id 做松散关联。

**formula_1 多题** — 需要关联 drivers → results → races → circuits 四张表，agent 经常跳过 results 或 races 导致关联路径错误。

### 根因分析

这是**知识图谱能力**问题。agent 需要知道：
1. 哪些表之间有外键关系（已有 FK 实体）
2. 某个关联是否需要经过中间表（桥接语义）
3. 直接 JOIN 和通过桥接表 JOIN 的区别

当前知识图谱中 FK 实体只存储了"这两个表有关联"的信息，但没有标注"这是多对多关系的中间表"或"这是必须经过的桥接路径"。

### 改进方向

- **已做**：修复了 extractor，让 FK/rel 实体连接到两端表（之前只连源表）；新增 `find_path` 工具发现间接 JOIN 路径
- **短期**：在 `_sql.py` 中更明确地引导 agent 在多表 JOIN 前先调 find_path 确认路径
- **中期**：在 extractor 阶段给中间表打上 "bridge table" 标签，让 agent 知道不能跳过
- **长期**：为每个 FK 关系生成自然语言描述（如 "connected 表记录了哪个原子参与了哪个键"），注入 schema 提示

---

## 三、列选择偏差（95 题，17.0%）

### 错误模式

1. **选错列** — name vs id、atom_id vs element、position vs positionText
2. **多加列** — golden 只要 1 列，agent 多加了名称/描述列
3. **少加列** — golden 返回多列，agent 遗漏了某些列
4. **列名歧义** — "atoms" 指 atom_id 还是 element？"id" 是 id 还是 player_api_id？

### 重灾区

- card_games（22 题）— 字段多（60+ 列）、命名不直观（uuid vs id vs name）
- formula_1（18 题）— position vs positionText、forename + surname vs fullname
- student_club（11 题）— status vs status_description

### 典型案例

**Q300** [toxicology] 问 "What atoms comprise TR186?"：
- Golden: `SELECT atom_id FROM atom WHERE molecule_id = 'TR186'`
- Predicted: `SELECT DISTINCT element FROM atom WHERE molecule_id = 'TR186'`
- 根因：golden 理解 "atoms" = 每个原子（atom_id），agent 理解为"什么元素"（element）

**Q15** [california_schools] 问学区的名称：
- Golden: `SELECT District FROM frpm`
- Predicted: `SELECT dname FROM schools`
- 根因：District 和 dname 指同一个概念但在不同表中

### 根因分析

核心是 **evidence 利用率不足**。BIRD 的 evidence 通常会明确指出应该用哪一列，但 agent 有时忽略 evidence 直接根据问题中的自然语言猜列名。此外，schema 提取时列的语义描述不够精确，agent 无法区分同义但不同名的列。

### 改进方向

- **短期（prompt）**：在 `_benchmark.py` 中强调"evidence 中明确提到的列名优先使用，不要根据语义猜测列名"
- **中期（工具使用）**：当 agent 对列名不确定时，引导它用 `query` 工具查 `SELECT DISTINCT <列名> LIMIT 5` 验证
- **长期（schema 语义）**：在提取阶段为列生成别名映射（如 District = dname），注入 agent prompt

---

## 四、问题理解偏差（89 题，15.9%）

### 错误模式

1. **遗漏条件** — 问题有 3 个限定条件，agent 只用了 2 个
2. **比较运算符搞错** — "after 1990" 用 `= '1990'`、"more than" 用 `>=`
3. **语义歧义** — "atoms" = 原子ID vs 元素、"average" = 全局平均 vs 分组后平均
4. **领域知识不足** — 不理解 "converted mana cost"、"fastest lap"、"UA 正常范围" 等领域术语

### 重灾区

- formula_1（17 题）— 赛车术语（constructor vs driver、lap vs race、position vs grid）
- card_games（11 题）— 卡牌术语（mana cost、rarity、color identity）
- european_football_2（9 题）— 足球统计口径（appearance、goal、assist 的定义）
- thrombosis_prediction（9 题）— 医学术语（正常范围、分组条件）

### 典型案例

**Q16** [california_schools] 问 "在 Lake County、测试人数小于 100、已合并的学校有多少"：
- Golden: 三个条件全部覆盖
- Predicted: 直接返回总行数，完全忽略了所有过滤条件
- 这是严重的问题理解偏差

**Q342** [card_games] 问 "cost more converted mana for the face"：
- Golden: `ORDER BY ... LIMIT 1` 取最小值（语义有争议）
- Predicted: 用 `MAX` 子查询取最大值（从字面理解更合理）
- 这种情况下 golden SQL 本身可能有争议

### 根因分析

这类错误最难修，因为它本质是**模型推理能力**的上限。89 题里只有 75 题标为可修复，14 题认为无法修复。领域知识问题（什么是 "converted mana cost"）很难通过通用 prompt 解决。

### 改进方向

- **短期（prompt）**：强调"逐条拆解问题中的每个限定条件，确保 WHERE 子句覆盖所有条件"
- **中期（工具使用）**：当 agent 不确定领域术语时，引导它查 evidence 中的解释
- **局限**：除非针对每个数据库写专项提示词，否则领域知识类错误的改进空间有限

---

## 五、输出格式差异（75 题，13.4%）

### 错误模式

1. **GROUP_CONCAT 合并** — golden 返回多行，agent 用 GROUP_CONCAT 合并为一行
2. **多列展开** — golden 返回两行一列，agent 返回一行两列
3. **多余变换** — agent 添加了 ROUND、文本拼接、别名美化，golden 没有
4. **多加排序列** — agent 加了 ORDER BY 导致输出顺序与 golden 不同

### 重灾区

- student_club（16 题）— 大量 GROUP_CONCAT 误用
- formula_1（12 题）— 多列展开、文本拼接
- card_games（11 题）— name 拼接、格式变换

### 典型案例

**Q307** [toxicology] 问 "Name the atoms' elements that form bond TR000_2_3"：
- Golden: 返回两行 element（c, cl）
- Predicted: 返回一行 (c, cl) 两列

**Q23** [california_schools] 问街道地址：
- Golden: `SELECT Street` — 只输出街道字段
- Predicted: `SELECT Street || ', ' || City || ', ' || State || ' ' || Zip` — 拼接完整地址

### 根因分析

这是**最容易修复**的类别。SQL 逻辑本身是对的，只是输出格式不对。75 题全部标为可修复。

### 改进方向

- **已在 `_benchmark.py` 中添加的规则**：
  - "不要用 GROUP_CONCAT 把多行结果合并为一行"
  - "返回多个值时用单列多行输出，不要横向展开为多列"
  - "不做多余变换 — 不要 ROUND、不要拼接多列"
- **效果预期**：这 75 题应该能在下一轮 benchmark 中看到明显改善

---

## 六、聚合逻辑偏差（59 题，10.6%）

### 错误模式

1. **COUNT vs COUNT DISTINCT** — golden 不去重，agent 去重（或反过来）
2. **GROUP BY 粒度不同** — golden 按 A+B 分组，agent 只按 A 分组
3. **AVG 层级错误** — "每个 X 的平均 Y"应该先 GROUP BY X 再 AVG，agent 直接全局 AVG
4. **SUM vs COUNT 选择错误** — 用 SUM(CASE WHEN) 还是 COUNT(DISTINCT CASE WHEN)

### 重灾区

- thrombosis_prediction（20 题）— 医学统计口径复杂，按性别分组 vs 全局统计
- codebase_community（7 题）— 投票、标签等多对多关系的聚合粒度
- toxicology（6 题）— 按分子维度 vs 按键维度聚合

### 典型案例

**Q197** [toxicology] 问 "average number of oxygen atoms in single-bonded molecules"：
- Golden: 先按分子 GROUP BY 算每分子氧原子数，再 AVG
- Predicted: 用 correlated subquery + DISTINCT 分子集，分子集合可能不同

**Q310** [toxicology] 问 "how many molecules have double bond + how many carcinogenic"：
- Golden: `COUNT(DISTINCT molecule_id), SUM(CASE WHEN label='+' THEN 1 ELSE 0 END)`
- Predicted: `COUNT(DISTINCT molecule_id), COUNT(DISTINCT CASE WHEN label='+' THEN molecule_id END)`
- 差异：SUM 可能重复计数（多条 double bond），COUNT DISTINCT 更精确

### 根因分析

聚合问题的关键是**理解"对什么粒度聚合"**。BIRD 的 golden SQL 有时对 DISTINCT 的使用比较随意，agent 的写法可能更正确但结果不同。证据提示有时会给出明确公式，agent 需要严格遵循。

### 改进方向

- **已在 `_benchmark.py` 中添加**：
  - "evidence 中如果给出了明确的数学公式，必须严格将公式翻译为 SQL"
  - "除非问题明确要求'不同的'或'唯一的'，不要加 DISTINCT"
- **中期**：对多层聚合（AVG of AVG）给出更明确的引导

---

## 七、百分比计算偏差（29 题，5.2%）

### 错误模式

1. **分子分母来自不同数据集** — 分子来自子查询结果、分母来自全表 COUNT(*)
2. **百分比 vs 小数** — golden 乘以 100，agent 不乘（或反过来）
3. **计算维度不同** — 按 bond 维度还是 molecule 维度计算占比

### 重灾区

- toxicology（6 题）— 分子维度（含某元素的分子数）vs 键维度（某类键数）
- card_games（4 题）— 按 card 维度 vs set 维度
- codebase_community（4 题）— 按用户维度 vs 标签维度

### 典型案例

**Q286** [toxicology] 问 "what percent of compounds form a triple-bond"：
- Golden: `CAST(COUNT(CASE WHEN bond_type='#' THEN bond_id END) AS REAL) * 100 / COUNT(bond_id) FROM bond` — 按 bond 维度
- Predicted: `CAST(COUNT(DISTINCT CASE WHEN bond_type='#' THEN molecule_id END) AS REAL) * 100.0 / COUNT(DISTINCT molecule_id)` — 按 molecule 维度

**Q298** [toxicology] — golden 的分母是含氢致癌分子集合内的总数（同一子集），agent 的分母是全表 `SELECT COUNT(*) FROM molecule`。

### 根因分析

核心问题是 **"百分比的基数应该是什么"**。BIRD golden 倾向于在同一个数据集内（子查询结果内）计算分子分母，agent 经常把分母设为全表或另一个更大的集合。29 题全部可修复。

### 改进方向

- **已在 `_benchmark.py` 中添加**：
  - "百分比的分子和分母必须来自同一数据集（同一子查询或同一 JOIN 结果），不要把分母设为全表 COUNT(*)，除非问题明确说'占所有...的比例'"
  - "CAST(COUNT(...) AS REAL) * 100 / COUNT(...)，注意用 REAL 避免整数除法截断"

---

## 效率分析

### 正确案例的工具调用效率

抽样 55 条正确案例：平均 20.7 次工具调用、耗时 117.3 秒，只有 6/55 被判定合理。

| 数据库 | 平均调用 | 平均耗时 | 合理率 |
|---|---|---|---|
| european_football_2 | 10.8 | 154.5s | 3/5 |
| superhero | 16.8 | 60.1s | 0/5 |
| toxicology | 16.4 | 64.8s | 2/5 |
| codebase_community | 13.6 | 87.9s | 1/5 |
| card_games | 19.8 | 196.3s | 0/5 |
| financial | 23.0 | 79.6s | 0/5 |
| student_club | 21.0 | 135.0s | 0/5 |
| california_schools | 23.4 | 123.7s | 0/5 |
| debit_card_specializing | 28.4 | 86.3s | 0/5 |
| formula_1 | 27.2 | 192.6s | 0/5 |
| thrombosis_prediction | 27.8 | 109.1s | 0/5 |

### 效率问题模式

1. **重复 glob 探索** — 同一个目录 glob 多次，没有缓存结果
2. **冗余 query 验证** — 已经拿到 schema 信息后，还反复执行 `SELECT * FROM t LIMIT 1` 验证
3. **试探性 SQL** — 先写一个草稿 SQL 执行看结果，再修改，往返多次

理想情况下简单问题 3-5 次调用（1 次 glob schema + 1 次 query 验证 + 1 次执行），复杂问题 8-12 次。当前平均 20.7 次有明显压缩空间。

### 改进方向

- **减少 glob 调用**：一次 glob 获取完整 schema，不要分多次查
- **减少 query 验证**：如果 schema 信息已经足够，直接生成最终 SQL
- **prompt 引导**：在 `_benchmark.py` 中明确"先用工具了解数据库结构，再一次性生成 SQL"

---

## 修复优先级与预期效果

| 优先级 | 类别 | 题数 | 改进手段 | 状态 |
|---|---|---|---|---|
| P1 | 输出格式差异 | 75 | prompt 规则（已在 `_benchmark.py` 中添加） | 已完成，待验证 |
| P1 | 百分比计算偏差 | 29 | prompt 规则（已在 `_benchmark.py` 中添加） | 已完成，待验证 |
| P2 | JOIN 路径差异 | 100 | `find_path` 工具 + prompt 引导 | 工具已完成，待验证 |
| P2 | 列选择偏差 | 95 | 强化 evidence 利用 + query 验证 | 待实施 |
| P3 | WHERE 条件差异 | 111 | prompt 纪律 + query 验证 | 待实施 |
| P3 | 聚合逻辑偏差 | 59 | prompt 规则 + evidence 公式引导 | 部分已添加 |
| P4 | 问题理解偏差 | 89 | 受限于模型推理能力，改进空间有限 | 通用 prompt 优化 |

P1 的 104 题（输出格式 + 百分比）改进已在 `_benchmark.py` 中完成，下一轮 benchmark 应该能看到这部分的明显改善。P2 的 find_path 工具也已实现但还没在新 benchmark 中验证过效果。
