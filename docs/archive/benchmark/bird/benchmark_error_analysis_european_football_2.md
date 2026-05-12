# BIRD Benchmark 错误归因分析 — european_football_2

## 版本对比

| 版本 | 正确率 | 变化 |
|---|---|---|
| v1（无 query 工具，旧 benchmark prompt） | 88/129 (68.2%) | — |
| v2（+ query 工具，+ 新 benchmark prompt） | 91/129 (70.5%) | +3 |

**新增正确**：Q1020, Q1037, Q1060, Q1068, Q1098（修 5 题，退 1 题）
**退步**：Q1141

---

## 错误分类（v2, 38 题错误）

| 类别 | 数量 | 占比 | 说明 |
|---|---|---|---|
| 列选择 / 聚合逻辑偏差 | 9 | 24% | SQL 正确但 SELECT 列、聚合方式与 golden 不同 |
| Evidence 误读 / question 理解偏差 | 6 | 16% | 误解 question 语义或盲从 evidence 错误描述 |
| 跨表 JOIN 路径未发现 | 6 | 16% | Player→Country 需经 Match 中转，模型找不到路径 |
| 行数偏差（语义争议） | 6 | 16% | DISTINCT / GROUP BY / LIMIT 差异 |
| SQL 逻辑错误 | 5 | 13% | 计算方式、条件实现与 golden 不同 |
| Golden SQL 本身有争议 | 3 | 8% | Golden SQL 的写法不合理或结果有误 |
| 超时 / max_rounds 耗尽 | 2 | 5% | query 工具消耗轮次，探索不够用 |
| Evidence 本身错误 | 1 | 3% | BIRD 标注错误 |

---

## 一、列选择 / 聚合逻辑偏差（9 题）

Agent 生成了完整正确的 SQL，但 SELECT 列、聚合方式与 golden 不同。

涉及：Q1021, Q1024, Q1027, Q1032, Q1034, Q1085, Q1086, Q1135, Q1140

**示例** — Q1021:
- Question: "height of the tallest player? Indicate his **name**."
- Golden: `SELECT player_name FROM Player ORDER BY height DESC LIMIT 1`
- Predicted: `SELECT player_name, height FROM Player WHERE height = (SELECT MAX(height))`
- **差异**：Agent 多选了 height 列。结果集值不同（包含 height 列导致行比对失败）

**示例** — Q1024:
- Question: "top 5 players...Indicate their **player id**"
- Golden: `SELECT id FROM Player_Attributes ORDER BY crossing DESC LIMIT 5`
- Predicted: `SELECT p.player_name, pa.player_api_id, MAX(pa.crossing) ... GROUP BY`
- **差异**：Golden 用 `id`（行级），Agent 用 `player_api_id` + GROUP BY（人级）

**示例** — Q1034:
- Golden: `SELECT player_api_id ... ORDER BY overall_rating DESC LIMIT 1`（只取 1 行）
- Predicted: `SELECT DISTINCT player_api_id ... WHERE overall_rating = (SELECT MAX(...))`
- **差异**：MAX 可能有多人同分，DISTINCT 返回多行 vs LIMIT 1 返回 1 行

**根因**：BIRD golden 的列选择倾向（用 `id` 而非 `player_api_id`、不加多余列、LIMIT 1 倾向）和 agent 的理解不完全一致。

**解决方案**：query 工具已帮助验证数据，但"选哪列"是理解层面的问题，prompt 需要进一步强调"只 SELECT 问题明确要求的列"。

---

## 二、Evidence 误读 / question 理解偏差（6 题）

涉及：Q1029, Q1041, Q1058, Q1092, Q1137, Q1148

**示例** — Q1029:
- Question: "**top 4** teams with highest buildUpPlaySpeed"
- Golden: `ORDER BY buildUpPlaySpeed ASC LIMIT 4`（取前 4 个）
- Predicted: `GROUP BY ... ORDER BY MAX(buildUpPlaySpeed) DESC`（只返回 1 行）
- **根因**：理解为"最高速度"而非"速度最高的 4 个队"

**示例** — Q1148:
- Question: "percentage of players that are under 180 cm who have overall strength > 70"
- Golden: `COUNT(CASE WHEN rating > 70 THEN id END) * 100 / COUNT(id) ... WHERE height < 180`（在 <180 的子集内算百分比）
- Predicted: `COUNT(DISTINCT p.id) * 100 / (SELECT COUNT(*) FROM Player) ... WHERE height < 180 AND rating > 70`（除以全表总数）
- **根因**：对"百分比"的分母理解不同

---

## 三、跨表 JOIN 路径未发现（6 题）

Player 和 Country 之间没有直接外键，需通过 Match 表中转。

涉及：Q1119, Q1120, Q1121, Q1126, Q1127, Q1131

**示例** — Q1126:
- Golden: `Player JOIN Match ON player_api_id = home_player_1 JOIN Country WHERE name = 'Belgium'`
- Predicted: Agent 找到了 Match 的 player 列但用了 `IN (m.home_player_1, m.home_player_2, ...)` 导致结果过多
- **进展**：Agent 现在**能发现** Match 表有 player 列（通过 query 工具验证），但 JOIN 方式与 golden 不同

**示例** — Q1131:
- Golden: `Player JOIN Match ON id = id JOIN Country`（用 Player.id = Match.id 这个可疑的 JOIN）
- Predicted: Agent 用了 `home_player_1` 做 JOIN
- **注意**：Golden SQL 本身的 JOIN 条件 `Player.id = Match.id` 语义上也很奇怪

**现状**：query 工具让 agent 能主动探索 Match 表结构，部分找到了路径但 JOIN 方式不同。根因仍是隐含关系（详见 `implicit_relationship_gap.md`）。

---

## 四、行数偏差（语义争议）（6 题）

涉及：Q1023, Q1063, Q1064, Q1080, Q1093, Q1136

**示例** — Q1063:
- Question: "Aaron Doran's potential score"
- Golden: 返回 7 行（所有历史记录）
- Predicted: 返回 1 行（最新一条）
- **Agent 的写法更合理**

**示例** — Q1080:
- Golden: `COUNT(player_api_id)`（含重复）
- Predicted: `COUNT(DISTINCT player_api_id)`（去重）
- **Agent 的写法更合理**

**示例** — Q1093:
- Golden: `SUM(overall_rating) / COUNT(id)`（手动算平均）
- Predicted: `AVG(overall_rating)`（用聚合函数）
- **Agent 的写法语义上等价，但结果可能有浮点精度差异**

这类错误大部分是 BIRD golden SQL 本身的写法偏好问题，agent 的 SQL 通常更合理。

---

## 五、SQL 逻辑错误（5 题）

涉及：Q1031, Q1094, Q1118, Q1134, Q1144

**示例** — Q1118:
- Question: "players who are 35 years old and above"
- Golden: `CAST((JULIANDAY('now') - JULIANDAY(birthday)) AS REAL) / 365 >= 35`
- Predicted: `datetime(CURRENT_TIMESTAMP,'localtime') - datetime(birthday) > 34`
- **差异**：日期计算方式不同，精度差异导致行数不同

**示例** — Q1134:
- Question: "difference between players 6 and 23's jumping scores"
- Golden: `SUM(CASE WHEN id=6 THEN jumping) - SUM(CASE WHEN id=23 THEN jumping)`（汇总所有记录）
- Predicted: 取最新一条的差（用子查询 ORDER BY date DESC LIMIT 1）
- Agent 用了 query 工具验证了数据，但选择了"最新记录"而非"所有记录汇总"

---

## 六、Golden SQL 本身有争议（3 题）

涉及：Q1107, Q1113, Q1135

**示例** — Q1113:
- Question: "Hannover 96 的 defence aggression class on 2015/9/10"
- Golden: 返回 `chanceCreationShootingClass`（射门类！不是防守类！）
- Predicted: 返回 `defenceAggressionClass`（语义正确的列）
- **Golden SQL 选错了列** — question 问 defence 但 golden 用了 chanceCreation

**示例** — Q1135:
- Question: "top five players with lowest potential, prefer right foot"
- Golden: `ORDER BY potential DESC LIMIT 5`（**DESC**！降序取最高的）
- Predicted: `ORDER BY MIN(potential) ASC LIMIT 5`（升序取最低的）
- **Golden 的排序方向与 question 矛盾** — question 说"lowest"但 golden 用了 DESC

---

## 七、超时 / max_rounds 耗尽（2 题）

涉及：Q1137, Q1148

Agent 使用 query 工具验证消耗了大量轮次。例如 Q1148，agent 执行了 6 次 query 调用反复验证百分比计算方式，最终在第 30 轮触发 stop prompt。

**解决方案**：query 工具带来验证能力但消耗轮次。可以考虑给 benchmark 模式适当提高 max_rounds（如 40）。

---

## 八、Evidence 本身错误（1 题）

**Q1061**：
- Question: "players whose first names are Adam and weigh more than 170"
- Evidence: "team_long_name; buildUpPlaySpeedClass = 'Fast'" — **完全无关的描述**
- Golden: `COUNT(id) WHERE player_name LIKE 'Adam%' AND weight > 170`
- Predicted: `COUNT(*) WHERE player_name LIKE 'Adam %' AND weight > 170`
- Agent 的 SQL 接近正确，但 `Adam %`（带空格）vs `Adam%`（无空格）导致结果不同

---

## v1 → v2 变化分析

### 修复的 5 题

| 题号 | v1 错因 | v2 修复原因 |
|---|---|---|
| Q1020 | 多选了 player_name | 新 prompt "只 SELECT 问题要求的列" 生效 |
| Q1037 | 660s 超时 | query 工具加速验证，在 30 轮内完成 |
| Q1060 | evidence 写 `= '1990'` 但问题说 after | 新 prompt "以问题语义为准" 生效 |
| Q1068 | 聚合方式不同 | query 工具验证后修正 |
| Q1098 | SQL 不完整 | query 工具帮助快速确认 Ajax 的数据 |

### 退步的 1 题

| 题号 | v1 正确 | v2 错误原因 |
|---|---|---|
| Q1141 | 正确 | 新 prompt "不做多余变换" 可能导致 agent 少了 DISTINCT |

### Query 工具的影响

- **正面**：Agent 能验证 SQL 结果，修了 3-5 题（Q1037、Q1068、Q1098）
- **负面**：消耗轮次（每次 query 验证算 1 轮），导致 2 题超时
- **中性**：对理解层面的错误（列选择、语义理解）帮助有限

---

## 解决方案优先级

| 优先级 | 方案 | 预期影响 |
|---|---|---|
| **P1** | 提高 benchmark max_rounds 至 40 | 减少 query 工具导致的超时（2 题） |
| **P1** | 强化 "只 SELECT 问题明确要求的列" | 减少列选择偏差（9 题） |
| **P2** | agent_analyze：发现 Player-Match-Country 隐含关系 | 解决跨表 JOIN（6 题） |
| **P2** | 区分 "golden 有争议" 和 "真正错误" | 3 题实际是 golden 问题 |
