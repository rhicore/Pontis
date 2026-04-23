# BIRD Benchmark 错误归因分析 — toxicology

## 总览

| 难度 | 正确 | 总数 | 准确率 |
|---|---|---|---|
| simple | 52 | 76 | 68.4% |
| moderate | 19 | 38 | 50.0% |
| challenging | 14 | 31 | 45.2% |
| **合计** | **85** | **145** | **58.6%** |

---

## 错误分类（60 题错误）

| 类别 | 数量 | 占比 | 说明 |
|---|---|---|---|
| 百分比计算偏差 | 12 | 20% | 分母选择、计算维度与 golden 不同 |
| JOIN 路径/方式差异 | 14 | 23% | 是否需要 connected 表、JOIN 粒度不同 |
| 列选择 / 输出格式 | 11 | 18% | 选错列、多加/少加列、行/列格式差异 |
| 输出格式差异 | 8 | 13% | 多行 vs 合并、单列 vs 多列 |
| 聚合逻辑偏差 | 6 | 10% | COUNT DISTINCT、GROUP BY 粒度差异 |
| WHERE 条件差异 | 4 | 7% | 条件组合、空值处理不同 |
| 问题理解偏差 | 4 | 7% | 语义模糊、特殊 SQL 技巧 |
| Golden 有争议 | 1 | 2% | golden SQL 本身语义模糊 |

---

## 一、百分比计算偏差（12 题）

toxicology 最大的错误来源。核心问题是"百分比的分子和分母分别应该是什么"。

涉及：Q201, Q218, Q219, Q254, Q263, Q273, Q286, Q287, Q298, Q317, Q324, Q330

**模式**：golden 通常用 `CAST(COUNT(...) AS REAL) * 100 / COUNT(...)` 在同一查询内计算，分母和分子来自同一个子集。agent 倾向于：
1. 分子分母来自不同子查询（分母用全表总数）
2. 计算维度不同（按 bond 维度 vs molecule 维度）

**示例** — Q286:
- Question: "what percent of compounds form a triple-bond"
- Golden: `CAST(COUNT(CASE WHEN bond_type='#' THEN bond_id END) AS REAL) * 100 / COUNT(bond_id) FROM bond` — **按 bond 维度**
- Predicted: `CAST(COUNT(DISTINCT CASE WHEN bond_type='#' THEN molecule_id END) AS REAL) * 100.0 / COUNT(DISTINCT molecule_id)` — **按 molecule 维度**

**示例** — Q298:
- Question: "percentage of molecules containing carcinogenic compounds that element is hydrogen"
- Golden: 分母是含氢的致癌分子集合内的总数
- Predicted: 分母是全表 `SELECT COUNT(*) FROM molecule`

**根因**：
1. BIRD golden 的百分比计算倾向于在同一个数据集内（子查询结果内）计算分子分母
2. Agent 经常把分母设为全表或另一个更大的集合
3. 百分比的"基数"理解不一致

---

## 二、JOIN 路径/方式差异（14 题）

toxicology 有 4 个表：atom、bond、connected、molecule。connected 是 atom 和 bond 之间的桥接表。golden 经常通过 connected 表做精确关联，agent 有时跳过 connected 直接 JOIN atom + bond。

涉及：Q207, Q233, Q234, Q243, Q259, Q260, Q269, Q284, Q309, Q321, Q326, Q328, Q337, Q338

**模式 A — 跳过 connected 表**：

**示例** — Q338:
- Question: "atom ID of double bonded carbon in TR012"
- Golden: `atom JOIN molecule JOIN bond JOIN connected ON atom_id = connected.atom_id WHERE bond_type = '=' AND element = 'c'` — 通过 connected 找**真正参与**双键的碳原子
- Predicted: `atom JOIN connected JOIN bond WHERE molecule_id = 'TR012' AND bond_type = '=' AND element = 'c'` — JOIN 条件不够精确，返回了不参与双键的碳原子

**模式 B — JOIN 粒度差异**：

**示例** — Q326:
- Question: "molecule with Sulphur atom with double bond"
- Golden: `atom JOIN bond ON atom.molecule_id = bond.molecule_id WHERE element = 's' AND bond_type = '='` — 同一分子同时有 S 和双键
- Predicted: 类似逻辑但返回了 47 行 vs golden 的更少行，可能 JOIN 条件不严格

**根因**：
1. connected 表的作用不容易从问题中推断 — 需要理解"某个原子是否真正参与某个键"
2. agent 倾向于通过 molecule_id 做"宽 JOIN"（同属一个分子），而非通过 connected 做"精确 JOIN"（真正连接）
3. 这和 european_football_2 的桥接表问题类似 — agent 不理解 connected 的桥接语义

---

## 三、列选择偏差（11 题）

选择了错误的列或多余的列。

涉及：Q217, Q231, Q248, Q250, Q252, Q257, Q267, Q283, Q296, Q300, Q305

**典型模式**：

| 模式 | 题号 | 说明 |
|---|---|---|
| 问 atom_id 返回 element | Q300 | "What atoms comprise TR186" → golden 用 atom_id |
| 问连接关系返回 element | Q217, Q248, Q252 | golden 返回 (atom_id, atom_id2)，agent 返回 (atom_id, element) |
| 多加列 | Q231, Q250, Q283, Q305 | golden 只要 1 列，agent 多加 |
| 少加列 | Q267, Q296 | golden 返回多列，agent 少返回 |
| 完全选错列 | Q257 | "List down atom id2" → agent 返回 atom_id |

**示例** — Q257:
- Question: "List down atom id2 for atoms with element sulfur"
- Golden: `SELECT DISTINCT T2.atom_id2 FROM atom T1 INNER JOIN connected T2 ON T1.atom_id = T2.atom_id WHERE T1.element = 's'`
- Predicted: `SELECT atom_id FROM atom WHERE element = 's'`
- **根因**：agent 没理解 "atom id2" 指的是 connected 表的 atom_id2 列

**示例** — Q300:
- Question: "What atoms comprise TR186?"
- Golden: `SELECT atom_id FROM atom WHERE molecule_id = 'TR186'`
- Predicted: `SELECT DISTINCT element FROM atom WHERE molecule_id = 'TR186'`
- **根因**：golden 理解 "atoms" = atom_id（每个原子），agent 理解为 "什么元素"

---

## 四、输出格式差异（8 题）

SQL 逻辑接近但输出行/列格式不同导致结果集比对失败。

涉及：Q211, Q216, Q230, Q237, Q268, Q271, Q306, Q307

**典型模式**：

| 模式 | 题号 | 说明 |
|---|---|---|
| 多行 vs 合并 | Q230, Q306 | golden 返回多行，agent 用 GROUP_CONCAT 合并为一行 |
| 两行 vs 一行两列 | Q268, Q307 | golden 返回两行 element，agent 返回 (element1, element2) 一行 |
| 文本变换 | Q237 | golden 用 IIF 变换为 YES/NO，agent 直接返回 + |
| 布尔 vs 详情 | Q271 | golden 返回具体列值，agent 返回布尔判断 |

**示例** — Q307:
- Question: "Name the atoms' elements that form bond TR000_2_3"
- Golden: 返回两行 element（c, cl）
- Predicted: 返回一行 (c, cl) 两列
- **根因**：数据格式不同，golden 倾向于"一列多行"，agent 倾向于"多列一行"

---

## 五、聚合逻辑偏差（6 题）

涉及：Q197, Q198, Q215, Q251, Q278, Q310

**示例** — Q197:
- Question: "average number of oxygen atoms in single-bonded molecules"
- Golden: 先按分子 GROUP BY 算每分子氧原子数，再 AVG
- Predicted: 用 correlated subquery + DISTINCT 分子集，分子集合可能不同

**示例** — Q310:
- Question: "how many molecules have double bond + how many carcinogenic"
- Golden: `COUNT(DISTINCT molecule_id), SUM(CASE WHEN label='+' THEN 1 ELSE 0 END)`
- Predicted: `COUNT(DISTINCT molecule_id), COUNT(DISTINCT CASE WHEN label='+' THEN molecule_id END)`
- **差异**：SUM 可能重复计数同一分子（多条 double bond），COUNT DISTINCT 更精确

---

## 六、WHERE 条件差异（4 题）

涉及：Q236, Q247, Q311, Q332

**示例** — Q332:
- Question: "molecules between TR004 to TR010, how many have single bonds"
- Golden: `WHERE molecule_id BETWEEN 'TR004' AND 'TR010' AND bond_type = '-'`
- Predicted: JOIN molecule + bond，用了额外的 JOIN 条件

**示例** — Q247:
- Question: "atoms that cannot bond with any other atoms"
- Golden: 排除能成键的 element 类型（element 维度）
- Predicted: 排除能成键的 atom_id（atom 维度）

---

## 七、问题理解偏差（4 题）

涉及：Q214, Q221, Q281, Q335

**示例** — Q281:
- Question: "Tally the toxicology element of the 4th atom of each molecule that was carcinogenic"
- Golden: 找每个致癌分子中 atom_id 序号为 4 的元素
- Predicted: 用 `substr(atom_id, -1) = '4'` 过滤 — 解析方式不同

**示例** — Q221:
- Question: "atoms bonded in TR001 with bond ID TR001_2_6"
- Golden: 用 `SUBSTR(bond_id, ...)` 从 bond_id 解析出原子 ID
- Predicted: 用 connected 表 JOIN — 逻辑不同

---

## 八、Golden 有争议（1 题）

**Q290**: "Which toxic element can be found in molecule TR151?"
- Golden: `SELECT DISTINCT element FROM atom WHERE molecule_id = 'TR151'` — 返回所有元素
- 预测: 同样的逻辑但结果不同
- **问题**：问 "toxic element" 但 golden 返回所有 element，包括非毒性元素

---

## 根因总结

| 层面 | 问题 | 影响 |
|---|---|---|
| **数据库理解** | connected 表的桥接语义不清晰，agent 经常跳过它 | 14 题 JOIN 路径错误 |
| **百分比计算** | agent 对百分比的分子分母理解与 golden 不一致 | 12 题 |
| **列选择** | "atoms" 是 atom_id 还是 element？问 atom_id2 返回 atom_id | 11 题 |
| **输出格式** | 多行 vs 一行多列、GROUP_CONCAT vs 多行 | 8 题 |
| **聚合维度** | 按分子维度 vs 按键维度、COUNT DISTINCT 粒度 | 6 题 |

### 核心问题

1. **connected 表语义理解不足** — agent 不清楚 connected 表是描述"哪个原子真正参与哪个键"的桥接表，倾向于直接通过 molecule_id 做"松散 JOIN"
2. **百分比计算规范缺失** — 没有明确指引百分比的分子分母应该来自同一子集
3. **列名歧义** — "atoms" 可能指 atom_id 或 element，"atom id2" 可能指 connected.atom_id2

### 修复优先级

| 优先级 | 方案 | 预期修复 |
|---|---|---|
| **P1** | prompt 中明确 connected 表的桥接语义 | ~8-10 题 JOIN 路径错误 |
| **P1** | 百分比计算规范：分子分母来自同一数据集 | ~8-10 题 |
| **P2** | 列选择引导：evidence 中提到的列名优先 | ~6-8 题 |
| **P2** | 输出格式规范：避免 GROUP_CONCAT，保持一行一实体 | ~5-6 题 |
