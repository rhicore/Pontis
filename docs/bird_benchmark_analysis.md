# BIRD Benchmark california_schools 错误分析

测试时间：2026-04-22  
模型：GLM-5.1（通过 OpenAI 兼容 API）  
结果：42 条完成，22 正确 20 错误，准确率 **52.3%**

| 难度 | 正确/总数 | 准确率 |
|------|-----------|--------|
| simple | 16/25 | 64.0% |
| moderate | 6/15 | 40.0% |
| challenging | 0/2 | 0% |

---

## 问题一：跨表 JOIN 数据格式不一致（CDSCode 前导零缺失）

### 现象

`satscores.cds` 与 `schools.CDSCode` 之间存在 FK 声明，但 `satscores.cds` 中 211 条记录缺少前导零（13位 vs 14位）。SQLite 默认不强制 FK（`PRAGMA foreign_keys = 0`），所以脏数据存在但 FK 声明仍在 schema 中。

- 总计 2269 条 satscores 记录，仅 2058 条能通过 `cds = CDSCode` 直接匹配（90.7%）
- 全部 211 条违规记录可通过 `'0' || cds = CDSCode` 或 `CAST(cds AS INTEGER) = CAST(CDSCode AS INTEGER)` 修复

### 影响范围

涉及所有 `satscores JOIN schools/frpm` 的 query，包括：Q7, Q10, Q15, Q16, Q17, Q19, Q33, Q36, Q37, Q41 等。

Agent 的表现：
- **Q10**: 发现了问题，用 `'0' || s.cds = f.CDSCode` 修复 — 但修复后的 JOIN 结果与 golden SQL 的直接 JOIN 不同（golden 也没处理这个问题）
- **Q15**: 用 `LIKE '%' || s.cds` 模糊匹配 — 结果偏了
- **Q16**: 用 `CAST AS INTEGER` 修复 — 又加入了 County='Alameda'（golden 用 'Lake'）
- **Q37/Q41**: 直接 `cds = CDSCode` JOIN — 因为 CDSCode 不以 0 开头的记录能匹配上，恰好结果对了

### 根因

BIRD 数据集的 `satscores.cds` 列部分值缺少前导零，属于数据质量问题。Agent 在运行时无法预知这个格式不一致，只能通过试查发现，导致大量 bash 调用和调试循环。

### 解决办法

**已实施**：新增 `db_fk_validate.py` 模块，在提取阶段校验 FK 实体的实际数据一致性，将 `match_rate`、`violation_count`、`format_hint` 写入 FK 实体 meta。

**待实施**：在 readonly prompt 或 benchmark prompt 中加入指引——"涉及 JOIN 时，先 glob 查看 FK 实体，读取 meta 中的 format_hint，按建议处理格式差异"。

**对准确率的影响**：排除此问题后，预计有 5-8 条 query 能修正，准确率提升约 10-15%。

---

## 问题二：语义相似但含义不同的列混淆

### 现象

Agent 反复混淆不同表中名称相似但语义不同的列：

| 问题中的概念 | Golden SQL 使用的列 | Agent 错误使用的列 | 涉及 Query |
|---|---|---|---|
| "direct charter-funded" | `frpm.\`Charter Funding Type\`` | `schools.FundingType` | Q4, Q17, Q31 |
| "Low Grade = 9, High Grade = 12" | `frpm.\`Low Grade\``, `frpm.\`High Grade\`` | `schools.GSoffered = '9-12'` | Q20 |
| "High schools" | `frpm.\`School Type\` = 'High Schools (Public)'` | `schools.EILName = 'High School'` | Q24 |
| "funding type" | `frpm.\`Charter Funding Type\`` | `schools.FundingType` | Q31 |
| "DOC type" | `schools.DOC` | `schools.DOCType` | Q25 |

### 根因

1. **列名相似度高**：`FundingType` 和 `Charter Funding Type` 字面上很像，但一个在 schools 表（表示学校整体资金来源，90%空值），一个在 frpm 表（表示特许学校资金类型，88%空值），含义完全不同
2. **Pontis 的 AI brief/detail 不够精确**：glob 返回的 brief 里只有一两句话的摘要，没有区分"哪个列对应哪个业务概念"
3. **Evidence 利用不足**：Q4 的 evidence 明确指出 `Charter School (Y/N) = 1 in the frpm`，暗示相关列都在 frpm 表，但 agent 没有据此推断 `Charter Funding Type` 也在 frpm

### 解决办法

1. **Prompt 层面**：在 benchmark prompt 中强调"注意 evidence 暗示的表归属，如果 evidence 指向 frpm 表的列，相关过滤条件也应优先在 frpm 表中寻找"
2. **工具层面**：meta 的 brief/detail 可以更明确地区分同义列的差异（这需要改进提取阶段的 AI 摘要质量）
3. **短期**：在 benchmark prompt 中给出具体例子——"不要混淆 `FundingType`（schools）和 `Charter Funding Type`（frpm），不要混淆 `GSoffered` 和 `Low Grade/High Grade`"

---

## 问题三：输出列选择/格式不匹配

### 现象

行数正确但列内容或格式不同，导致 tuple 比较失败：

| Query | 问题 | 行数 | 具体差异 |
|---|---|---|---|
| Q18 | 缺少 RANK() 列，多了 sname 列 | 50 vs 50 | Golden 输出 `(CharterNum, AvgScrWrite, RANK)`，Agent 输出 `(sname, AvgScrWrite, CharterNum)` |
| Q23 | 拼接了地址 | 1239 vs 1239 | Golden 输出 `(School, Street)`，Agent 输出 `(School, Street+City+State+Zip)` |
| Q25 | 列名混淆 | 57 vs 57 | Golden 输出 `DOC`，Agent 输出 `DOCType` |
| Q27 | 多了 School Name 列 | 2 vs 2 | Golden 只输出比率值，Agent 附带了学校名 |
| Q28 | 列来源不同 | 999 vs 999 | Golden 用 `frpm.\`School Name\``，Agent 用 `satscores.sname` |
| Q36 | 姓名格式不同 | 1 vs 1 | Golden 输出 6 个独立的 first/last 列，Agent 拼接成 `first last` 并过滤 NULL |
| Q41 | 列顺序不同 | 1 vs 1 | Golden 是 `(Street, City, State, Zip)`，Agent 是 `(Street, City, Zip, State)` |

### 根因

1. **Agent 自作主张美化输出**：拼接地址、拼接姓名、附加学校名等行为是 agent "想帮忙"但违反了 benchmark 的精确匹配要求
2. **列名相似混淆**：`DOC` vs `DOCType`，`School Name`（frpm）vs `sname`（satscores）vs `School`（schools）
3. **对问题理解不够精确**：Q23 问的是 "full street address"，Golden 只返回 Street 列，Agent 却拼接了完整地址

### 解决办法

1. **已实施**：在 benchmark prompt 中强调"只返回问题明确要求的列，不做拼接/美化/格式化"
2. **强化**：可以在 prompt 中增加"列顺序要与问题中提到的字段顺序一致"、"不要拼接多列为一个字段，除非问题明确要求"
3. **对于 Q25 这类**：需要在 prompt 中强调"同名/近名列要精确匹配问题要求的列名"

---

## 问题四：过滤条件多余或缺失

### 现象

| Query | 问题 | 差异 |
|---|---|---|
| Q7 | 添加了 `rtype = 'S'` | Golden 没有 rtype 过滤 |
| Q16 | County 用 'Alameda' | Golden 用 'Lake'（可能是 BIRD 标注问题） |
| Q26 | 缺少 `Website IS NOT NULL` | 多返回了 1 行 NULL 网站记录 |
| Q33 | 用 INNER JOIN 替代 LEFT JOIN + 额外 rtype 过滤 | 749 vs 8217 行，差距巨大 |
| Q37 | 用 ROW_NUMBER 替代 RANK + 额外 `IS NOT NULL` | 4 vs 34 行 |

### 根因

1. **Agent 添加"安全"过滤**：`rtype = 'S'`、`IS NOT NULL`、`> 0` 等条件，agent 认为是数据清洗，但 golden SQL 没有
2. **JOIN 类型选择错误**：Q33 应该用 LEFT JOIN 保留无 SAT 数据的学校，agent 用了 INNER JOIN 丢弃了这些记录
3. **窗口函数选择错误**：Q37 的 "top 5" 应该用 RANK（允许并列），agent 用了 ROW_NUMBER（不允并列），导致结果从 34 缩减到 4
4. **Q16 特殊**：golden SQL 的 County='Lake' 与问题中的 'Alameda' 矛盾，可能是 BIRD 标注错误

### 解决办法

1. **Prompt 层面**：
   - "不要自作主张添加安全过滤条件（如 `IS NOT NULL`、`> 0`、`rtype`），除非 evidence 明确要求"
   - "注意 JOIN 类型选择：问题说'list schools that...'如果包括没有关联数据的记录，用 LEFT JOIN"
   - "排名问题优先用 RANK() 而非 ROW_NUMBER()，除非问题明确要求不并列"
2. **Q16 是 BIRD 标注问题**，不计入 Pontis 失误

---

## 问题五：聚合逻辑错误

### 现象

**Q31** 是最典型的聚合逻辑错误：

- 问题："Riverside 中 average math score > 400 的学校"
- Evidence："Average of average math = sum(average math scores) / count(schools)"
- Golden SQL：`GROUP BY sname HAVING SUM(AvgScrMath)/COUNT(cds) > 400`（按学区聚合后过滤）
- Agent SQL：`WHERE AvgScrMath > 400`（逐行过滤，完全不同的语义）
- 结果：59 行 vs 6 行

### 根因

Agent 没有理解 "average of average" 的两层聚合含义，直接用了单行过滤。Evidence 已经明确给出了公式，但 agent 没有遵循。

### 解决办法

1. **Prompt 强化**：在 benchmark prompt 中强调"evidence 中的计算公式必须严格遵循，特别是涉及 AVG/SUM/COUNT 的聚合公式"
2. **Evidence 利用**：当 evidence 给出明确的数学公式时，agent 应该将其直接翻译为 SQL 的 GROUP BY + HAVING 逻辑

---

## 问题六：工具调用效率低下

### 现象

平均每个 query 13 次工具调用（其中 7 次 bash），最极端的 Q16 有 43 次工具调用（39 次 bash）。

### 典型模式

```
glob *.db::*.table          → 看表
bash .schema schools        → 看结构（冗余，glob 已提供）
bash .schema frpm           → 看结构（冗余）
bash SELECT ... LIMIT 5     → 试查
bash SELECT ... JOIN ...     → 试 JOIN
bash SELECT COUNT(*)         → 验证行数
... 反复多次 ...
最终输出 SQL
```

### 根因

1. **之前的 prompt 建议 "用 .schema 验证列名"** 导致 agent 不信任 glob/meta 的结构信息
2. **Agent 用 bash 试查代替直接生成 SQL**，每次 bash 调用 = 1 次 LLM round
3. **调试循环**：遇到 CDSCode 等数据问题时，agent 进入多轮试错

### 解决办法

1. **已实施**：删除 `.schema` 建议，新增"禁止用 bash 执行 SELECT 试查"
2. **已实施**：glob 默认返回条数从 100 降到 20，减少 token 消耗
3. **待观察**：下一轮 benchmark 应该能验证 bash 调用次数是否显著下降

---

## 问题总结与优先级

| 优先级 | 问题 | 影响范围 | 解决状态 |
|---|---|---|---|
| P0 | CDSCode 格式不一致（数据质量） | ~30% query | 已：db_fk_validate；待：prompt 引导读 FK meta |
| P1 | 语义相似列混淆 | ~15% query | 待：prompt 强化 evidence 利用 + 列名辨析 |
| P2 | 输出列格式不匹配 | ~15% query | 已：prompt 禁止美化；部分需进一步强化 |
| P2 | 过滤条件多余/缺失 | ~10% query | 待：prompt 强化 JOIN 类型和窗口函数选择 |
| P3 | 聚合逻辑错误 | ~5% query | 待：prompt 强化 evidence 公式遵循 |
| P3 | bash 试查过多 | 效率问题 | 已：prompt 禁止 bash 试查 |

### 预估改进效果

- 排除 CDSCode 数据质量问题（P0）：**+10-15%**
- 解决列混淆 + 列格式 + 过滤条件（P1-P2）：**+10-15%**
- 解决聚合逻辑（P3）：**+5%**
- 优化工具调用效率（P3）：不直接影响准确率，但减少 token 消耗和延迟

**理论上限**：解决所有问题后，california_schools 的准确率可能达到 **75-85%**（剩余错误来自 BIRD 标注问题和模型能力上限）。
