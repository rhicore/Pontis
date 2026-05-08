# BIRD Benchmark 错误分析报告

> 基于 1534 条 dev.json query，accuracy = 985/1534 = 64.2%

## 一、错误总览

| 分类 | 数量 | 占错误比 | 占总比 |
|------|------|---------|--------|
| **Schema 理解不足** | **301** | **54.8%** | **19.6%** |
| 非 Schema 错误（SQL 逻辑） | 244 | 44.4% | 15.9% |
| 解析/其他 | 4 | 0.7% | 0.3% |

Schema 理解不足是最大错误来源。细分为 6 个子类：

```
Schema 理解不足 (301)
├── 表选错                          169  (56%)
│   ├── 少了表（该用没用）             67
│   ├── 多了表（不该用用了）           41
│   └── 又多又少（完全选偏）           61
├── JOIN 关系错误                     72  (24%)
│   ├── 缺少 JOIN（知道表但不连）      46
│   ├── 同表对选错列                   6
│   └── 完全错误路径                  20
└── WHERE 条件中列理解不足             60  (20%)
    ├── 列混淆（选了语义相近但不同的列） 54
    └── 类型未知（字符串 vs 数字）        6
```

---

## 二、各类错误详解

### 2.1 表选错（169 cases）

**模式 A：少了表（67 cases）— 该用的表没用**

Agent 知道问题涉及哪些表，但遗漏了关联所需的中间表或维度表。

```
[financial] Q159: "List all withdrawals in cash transactions that client 3356 made"
  Golden 表: trans, client, account     （需要 account 桥接 trans 和 client）
  Predict 表: trans, client             （漏了 account，无法 JOIN）

[financial] Q144: "average credit card amount by account holders in a month"
  Golden 表: trans, card, account       （需要 card 表获取信用卡信息）
  Predict 表: trans                     （漏了 card 和 account）
```

**模式 B：多了表（41 cases）— 不该用的表用了**

Agent 倾向于"多关联保险"，看到 FK 就全用上。

```
[financial] Q107: "gender of the oldest client who opened account in highest avg salary district"
  Golden 表: client, district           （client 直接 JOIN district）
  Predict 表: client, district, disp, account  （多加了 disp 和 account）

[toxicology] Q210: "What atoms are connected in single type bonds?"
  Golden 表: bond, connected            （只需要这两张表）
  Predict 表: bond, connected, atom     （多加了 atom）
```

**模式 C：又多又少（61 cases）— 完全选偏**

Agent 理解偏了问题意图，选了完全不同的表。

```
[california_schools] Q9: "schools with Math avg > 560, how many have free meal students"
  Golden 表: satscores, frpm            （SAT 数据关联午餐数据）
  Predict 表: satscores, schools        （用 schools 替代了 frpm）

[financial] Q109: "clients opened accounts in Jesenik branch were women"
  Golden 表: client, district           （client 直接查 district）
  Predict 表: disp, account             （完全绕开了 client，走了 disp→account）
```

---

### 2.2 JOIN 关系错误（72 cases）

**模式 A：缺少 JOIN（46 cases）— 知道要用哪些表但不连**

这是 JOIN 错误中最常见的。Agent glob 到了表和 FK，但写 SQL 时没有写 JOIN。

```
[california_schools] Q19: "phone number of school with highest Math avg"
  Golden JOIN:  satscores.cds = schools.CDSCode
  Predict JOIN: (空 — 根本没 JOIN，直接从 satscores 查)

[california_schools] Q7: "phone of school with most test takers scoring > 1500"
  Golden JOIN:  satscores.cds = schools.CDSCode
  Predict JOIN: (空)
```

FK 存在且 agent 可能 glob 看到，但没有在 SQL 中使用。

**模式 B：同表对选错列（6 cases）**

Agent 用了正确的表对，但 JOIN 列选错了。

```
[california_schools] Q15: "Which active district has highest Reading score?"
  Golden:  satscores.cds = schools.CDSCode    （用 CDSCode 关联）
  Predict: satscores.dname = schools.District  （用学区名关联）
  ↑ 这正是 REL 实体 satscores.dname__to__schools.District.rel 误导的

[codebase_community] Q581: "posts with most comments"
  Golden:  posts.owneruserid = users.id        （用 owner 关联）
  Predict: posts.lasteditoruserid = users.id    （用 last editor 关联）
```

**模式 C：完全错误路径（20 cases）**

Agent 选了完全不同的 JOIN 路径。

```
[formula_1] Q905: "driver with most wins in 2008"
  Golden:  drivers → driverstandings → races    （通过 driverstandings 桥接）
  Predict: races → results                      （跳过了 driverstandings）

[codebase_community] Q632: "posts edited by users with highest reputation"
  Golden:  posthistory.postid = votes.postid AND posthistory.userid = users.id
  Predict: users.id = votes.userid              （完全不同的关联路径）
```

---

### 2.3 WHERE 条件中的列理解不足（60 cases）

**模式 A：列混淆（54 cases）— 语义相近但不同的列**

这是 WHERE 错误中最大的子类。数据库中存在多对语义重叠的列，agent 选错了。

```
# 代码列 vs 名称列
[california_schools] Q70: "District Community Day Schools"
  Golden: SOC = 69                             （用代码值）
  Predict: SOCType = 'District Community Day Schools'  （用名称列）
  ↑ Agent 不知道 SOC=69 对应 District Community Day Schools

[california_schools] Q26: "high schools in Monterey"
  Golden: "School Type" = 'High Schools (Public)'   （用类型名称列）
  Predict: EILCode = 'HS'                           （用教育等级代码列）
  ↑ School Type 和 EILCode 都能表达"高中"，但含义不同

# 数值列混淆
[california_schools] Q26: "free meal count > 800"
  Golden: "Free Meal Count (Ages 5-17)" > 800     （免费午餐人数）
  Predict: "FRPM Count (Ages 5-17)" > 800          （免费+减价午餐人数）
  ↑ Free Meal Count ≠ FRPM Count，后者数值更大

[financial] Q102: "accounts with amount > 3000"
  Golden: trans.amount > 3000                      （交易金额）
  Predict: trans.balance > 3000                    （账户余额）
  ↑ 同一张表两个数值列

# 跨表同名列
[california_schools] Q33: "school names with free meal 1900-2000"
  Golden: SELECT frpm."School Name"                （从 frpm 取）
  Predict: SELECT schools.School                   （从 schools 取）
  ↑ 两列语义相同但分属不同表，golden 要求特定一个

# 代码列 vs 名称列（输出）
[california_schools] Q28: "locally funded schools"
  Golden: SELECT School, DOC                       （输出代码）
  Predict: SELECT School, DOCType                  （输出了名称）
```

**模式 B：类型未知（6 cases）— 不知道列存的是字符串还是数字**

```
[california_schools] Q32: "top 5 schools in grades 1-12"
  Golden: WHERE SOC = 66       （无引号）
  Predict: WHERE SOC = '66'    （有引号）
  ↑ SQLite 中 SOC 列存的是 TEXT，golden 用了隐式类型转换，agent 用了引号导致不匹配

[california_schools] Q46: "State Special Schools"
  Golden: WHERE DOC = 31       （无引号）
  Predict: WHERE DOC = '31'    （有引号）
```

---

## 三、FK/REL/OVERLAP 覆盖率分析

### 3.1 关系实体对实际 JOIN 的覆盖

| 来源 | 覆盖唯一 JOIN | 占比 |
|------|--------------|------|
| 仅 FK | 83 | 72.8% |
| 仅 REL（不含 FK） | 8 | 7.0% |
| 仅 OVERLAP（不含 FK/REL） | 7 | 6.1% |
| 总覆盖 | 98 | 86.0% |
| 未覆盖 | 16 | 14.0% |

### 3.2 REL 的实际贡献

- REL 实体去重后 unique 关系 20 个（california_schools 为例）
- 其中仅 1 个被实际 SQL 用到（satscores.cds↔frpm.CDSCode）
- **REL 直接导致错误 JOIN 的比例：0%**（6 个 JOIN 错误 case 中无一是被 REL 误导）
- REL 的主要问题是**噪音**：大量无关 REL 在 glob 结果中占据 token，间接干扰 agent 判断

### 3.3 FK 读取与错误的关系

| Schema 错误 | meta 读了 FK | glob 看到 FK 但没读 | 完全没碰 FK |
|------------|-------------|-------------------|------------|
| 所有 schema 错误 (241) | 129 (53.5%) | 88 (36.5%) | 24 (10.0%) |

**53.5% 的 schema 错误是 agent 读了 FK 详情后仍犯的**。问题不是"没读 FK"，而是读了也不会正确使用。

---

## 四、优化方案

### 方案 1：列元数据增强（解决列混淆 — 54 cases）

**问题**：Agent 分不清 School Type vs EILCode、Free Meal Count vs FRPM Count、SOC vs SOCType。

**做法**：
- 在 `.col` 的 detail 中标注**使用场景区分**，如：
  - `SOC` col: "学校类型代码（如 69=District Community Day Schools）。当问题提到具体类型名称时，用本列配合代码值；不要与 SOCType（类型名称列）混淆"
  - `SOCType` col: "学校类型的文字描述。当需要 WHERE 过滤特定类型时，优先用 SOC 代码列而非本列"
- 在消歧实体（`.disambig`）中增加**使用指引**，而非仅标注"这两列不同"

**预期收益**：54 个列混淆错误中估计可修复 ~30 个（2% accuracy 提升）

### 方案 2：代码值映射标注（解决类型未知 + 部分列混淆 — ~20 cases）

**问题**：Agent 不知道 SOC=69 对应什么，也不知道 SOC 存的是 TEXT 而非 INT。

**做法**：
- 在 `.col` 的 meta 中增加 `value_type` 字段，明确标注 SQLite 实际存储类型
- 在 low-cardinality 列（如 SOC、DOC、StatusType）的 meta 中增加 `common_values` 字段，列出 top-K 值及含义
- 当前 topk 已有部分实现，但需要更明确的**代码→含义**映射

**预期收益**：~20 cases（1.3% accuracy 提升）

### 方案 3：FK 信息内联到表 meta（解决缺少 JOIN — 部分 46 cases）

**问题**：Agent glob 到表后知道有哪些列，但不知道 JOIN 关系。FK 是独立实体需要额外 glob+meta。

**做法**：
- 在 `.table` 的 detail 中直接嵌入 FK 摘要，如：
  - `satscores.table` detail: "本表通过 cds 列关联 schools.CDSCode（FK），通过 cds 列关联 frpm.CDSCode（FK）"
- Agent 读表 meta 时自动获得 JOIN 信息，无需额外步骤

**预期收益**：缺少 JOIN 的 46 cases 中，部分可修复（估计 ~20 个，1.3% accuracy 提升）

### 方案 4：REL 过滤/降权（减少噪音干扰）

**问题**：california_schools 有 19 个 REL，仅 1 个被实际 SQL 使用。大量 REL 在 glob 结果中制造噪音。

**做法**：
- Phase 8（REL 生成）提高阈值，过滤掉 Jaccard < 0.1 或 coverage < 0.2 的弱关系
- REL 的 brief/detail 中增加**置信度标注**和**使用限制**，如"此关系为语义推断，置信度 0.6，仅作辅助参考，不应作为主 JOIN 条件"
- 在 agent prompt 中区分 FK（可靠）和 REL（参考级）

**预期收益**：间接改善，难以精确量化

### 方案 5：Prompt 策略优化（解决过度条件化 + 部分逻辑错误）

**做法**：
- 增加"不要加问题没要求的过滤条件"规则（如不要自动加 rtype='S'）
- 增加"遵循 evidence 中的计算公式"规则
- 在写 SQL 前增加"规划步骤"：先明确需要哪些表、哪些 JOIN、哪些 WHERE 条件

**预期收益**：~20-30 cases（1-2% accuracy 提升）

---

## 五、预期收益汇总

| 方案 | 目标错误 | 估计修复 | Accuracy 提升 |
|------|---------|---------|--------------|
| 列元数据增强 | 54 列混淆 | ~30 | +2.0% |
| 代码值映射 | 20 类型/代码 | ~20 | +1.3% |
| FK 内联到表 meta | 46 缺少 JOIN | ~20 | +1.3% |
| REL 过滤/降权 | 噪音 | 间接 | ~+0.5% |
| Prompt 策略 | 多类 | ~25 | +1.6% |
| **合计** | | **~95** | **64.2% → ~70%** |

优化后预期 accuracy 约 70%，接近 BIRD benchmark SOTA（~73%）。剩余 ~30% 错误是 LLM 的 NL→SQL 推理瓶颈，需要更强的模型或更复杂的推理策略。
