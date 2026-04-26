# Formula_1 Benchmark 错误分析报告

> 基于 174 条测试日志（92 correct, 80 wrong, 1 error, 1 timeout）
> DeepSeek 逐条生成文字总结后人工通读分析

---

## 一、总体结果

| 指标 | 数值 |
|------|------|
| 总题数 | 174 |
| 正确 | 92 (52.9%) |
| 错误 | 80 (45.9%) |
| 异常/超时 | 2 (1.2%) |
| 平均耗时(正确) | 70.9s |
| 平均耗时(错误) | 108.8s |
| 平均工具调用(正确) | 23.7 次 |
| 平均工具调用(错误) | 33.1 次 |

按难度分布（错误/总数）：
- simple: 54 错 / ~120 总 (~45% 准确率)
- moderate: 19 错 / ~40 总 (~52% 准确率)
- challenging: 7 错 / ~14 总 (~50% 准确率)

simple 题目占比最大且错误最多，说明主要瓶颈不在题目难度本身。

---

## 二、错误原因分类（非预定义，从日志中涌现）

### 2.1 错选表 — 最普遍的错误来源（~25 题）

模型在多张含义相近的表之间选错了数据源：

| 问题意图 | 模型用的表 | 应该用的表 | 典型题号 |
|----------|-----------|-----------|---------|
| 车手积分/排名 | `results` | `driverStandings` | Q891, Q892, Q893, Q905, Q902, Q903 |
| 圈速/圈数 | `results.fastestLapTime` | `lapTimes.time/milliseconds` | Q878, Q908 |
| 网页链接 | `races.url` | `circuits.url` | Q849, Q855, Q921 |
| 车队排名 | `circuits.location` | `constructorStandings.position` | Q851 |

**根因**：模型没有充分理解表的业务语义差异。`results` 记录单场比赛结果，`driverStandings` 记录赛季积分排名，两者都有 `points` 和 `position` 字段但含义完全不同。Guardrail 要求模型先读 meta，但模型读完 meta 后仍然做出错误选择。

### 2.2 时间格式不匹配（~5 题）

用户输入 `0:01:40`（hh:mm:ss），数据库存储 `1:40.014`（m:ss.mmm）。标准 SQL 用 `LIKE '1:40%'`，模型用 `= '0:01:40'` 精确匹配，返回空结果。

典型题号：Q860 (574s/104次工具), Q871 (470s/85次工具)

这类题模型耗费极长时间反复尝试不同格式，但始终无法自行发现应该用模糊匹配。

### 2.3 输出格式偏差（~8 题）

SQL 逻辑正确但输出格式与 golden 不一致：
- 姓名拼接 `forename || ' ' || surname` vs 两列分开输出（Q874, Q888）
- 缺少 `DISTINCT`（Q855, Q921）
- 选错输出列：`year` vs `date`（Q889），多了 `dob` 列（Q865）
- 输出列顺序不同（Q888）

### 2.4 条件差异（~10 题）

WHERE 条件理解偏差：
- `position IS NULL` vs `time IS NULL`（Q876："未完赛"的判断条件）
- `laps` vs `COUNT(lap)`（Q908：圈数计算方式）
- 年份范围理解偏差（Q922："2010's" 理解为 2010-2019 还是仅 2010）
- `fastestLapSpeed` 文本排序 vs 数值排序（Q879, Q927）

### 2.5 聚合逻辑偏差（~8 题）

- `COUNT(*)` vs `COUNT(CASE WHEN ... END)`（Q881）
- `MAX(round)` vs `COUNT(round)`（Q886）
- 百分比计算：分子分母来源不同（Q896）
- `SUM(wins)` vs `COUNT(*)`（Q903）

### 2.6 问题理解偏差（~6 题）

- "more information about races" → `circuits.url` 还是 `races.url`（Q849, Q921）
- "completion rate" 是单场还是职业生涯（Q881）
- "best lap time" 取 `lapTimes` 还是 `results`（Q878）
- "positions" 是排名还是地理位置（Q851）

### 2.7 系统级问题（2 题）

- Q894: 5 次 block，29 次工具调用后仍未产出 SQL (ERROR)
- Q1016: 19 次 block，82 次工具调用，完全跑飞 (UNKNOWN/timeout)

---

## 三、Guardrail 机制影响分析

### 3.1 核心数据

| Guardrail 类型 | 总拦截次数 |
|---------------|----------|
| SQLEntityCheck | 460 |
| BridgeTableCheck | 321 |
| SQLDisambigCheck | 151 |
| **合计** | **932** |

**拦截次数与准确率的强负相关**：

| 拦截次数 | 准确率 | 题数 |
|---------|--------|------|
| 0 次 | 100% | 6 |
| 1-3 次 | 68% | 59 |
| 4-7 次 | 46% | 67 |
| 8-12 次 | 44% | 32 |
| 13+ 次 | 10% | 10 |

### 3.2 Guardrail 的正面作用

1. **防止盲猜 SQL**：强制模型先读 meta 再执行 SQL，避免在未理解 schema 的情况下直接执行
2. **消歧提醒**：SQLDisambigCheck 在 `position`、`points` 等多义字段存在时提醒模型注意（如 Q858）
3. **JOIN 路径验证**：BridgeTableCheck 防止模型凭空 JOIN 无关联的表

### 3.3 Guardrail 的负面作用

#### 问题 1：模型面对 block 的行为不是"读 meta 然后重试"，而是"换一条完全不同的 SQL"

典型模式（Q902, 18 blocks）：
1. 模型生成 SQL A → SQLEntityCheck block（缺少 meta）
2. 模型读了一些 meta → 生成 SQL B（完全不同于 A）
3. SQL B 也被 block（新的未读实体）
4. 循环往复，每次都换 SQL

**结果**：模型在 65 次工具调用、18 次 block 后仍未收敛到正确答案。拦截没有帮模型纠正方向，反而让它反复更换策略。

#### 问题 2：高 block 数 = 模型已经困惑

block 次数高并不代表 guardrail 在"保护"——更多时候说明模型已经对 schema 理解不清，反复在错误的路径上试探。13+ 次拦截的题目只有 10% 正确率，说明这些题模型根本没找到正确方向。

#### 问题 3：效率开销巨大

168/174 (97%) 的题目被拦截过。即使对于最终正确的题目：
- 平均被拦截 3.2 次
- 平均工具调用 23.7 次
- 平均耗时 70.9s

大量时间花在 `glob` + `meta` 的机械式探索上。许多总结都指出"大量冗余的元数据查询"、"重复获取同一列信息"。

#### 问题 4：Guardrail 没有解决核心问题——错选表

在 ~25 题错选表的案例中，guardrail 强制模型读了 meta，但模型读完 meta 后仍然选择了错误的表（如选 `results` 而非 `driverStandings`）。说明 guardrail 只确保了"读了"，但不保证"理解了"。

### 3.4 Guardrail 两个极端案例

**Q933 (16 blocks → CORRECT)**：
- 被拦截 16 次，工具调用 45 次，耗时 169s
- 最终正确，但过程极其低效
- 说明即使答案正确，guardrail 也导致大量不必要的探索

**Q902 (18 blocks → WRONG)**：
- 被拦截 18 次，工具调用 65 次，耗时 226s
- 模型在 `results` 和 `driverStandings` 之间反复切换
- Guardrail 没有能力判断"你应该查 driverStandings 而不是 results"

---

## 四、关键发现总结

1. **最大的错误来源是错选表（~25 题）**，而非 SQL 语法、JOIN 路径或聚合逻辑。核心是 `results` vs `driverStandings` 的混淆。
2. **Guardrail 拦截次数与准确率强负相关**，但不是因果关系——高 block 数反映的是模型对 schema 理解不足，guardrail 只是放大了这个问题。
3. **Guardrail 的核心价值是"强制读 meta"，但读完 meta 后模型的理解质量是瓶颈**。目前 guardrail 无法评估模型是否真正理解了 meta 内容。
4. **时间格式匹配** 是一类特殊错误，模型难以自行发现应该从精确匹配切换到 `LIKE` 模糊匹配。
5. **输出格式偏差** 导致约 8 题逻辑正确但结果被判错，说明 benchmark 对输出格式有严格要求。
6. **全局效率极低**：平均 23.7 次工具调用（正确题）和 70.9 秒，对于 simple 级别题目严重偏长。

---

## 五、改进方向建议

### 5.1 针对"错选表"问题（预期提升 ~15%）

`driverStandings` vs `results` 的混淆占错误的大头。改进方向：
- 在 meta 信息中增加表间语义对比描述（如"driverStandings 是赛季累计积分排名，results 是单场比赛结果"）
- 当模型查询涉及 `points` 或 `position` 字段时，主动提示消歧信息

### 5.2 针对 Guardrail 机制优化

- **限制同一题的最大 block 次数**（如 5 次后降级为 warn），防止无限循环
- **减少重复 block**：如果同一类 guardrail 已对同一 SQL block 过一次，后续不再 block
- **给 benchmark 模式加轮次上限**（当前 `effort="max"` 对应 `max_rounds=0`，无上限）

### 5.3 针对时间格式匹配（预期提升 ~3%）

在 meta 中增加列的格式说明和匹配建议，或在 prompt 中明确提示"时间格式可能不一致，优先使用 LIKE 模糊匹配"。

### 5.4 针对输出格式

在 benchmark prompt 中增加格式要求："只输出必要的列，不要拼接或增加额外列，必要时使用 DISTINCT"。

### 5.5 效率优化

当前简单题平均 23.7 次工具调用过多。理想情况：
- simple: 5-10 次
- moderate: 10-15 次
- challenging: 15-20 次

可通过缓存 meta 信息、减少 glob 扫描次数来降低。
