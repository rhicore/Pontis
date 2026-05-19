# KDD Public Reflection 可迁移提示词

来源：`kdd_public_reflection_20260519_220153`。本轮 50 个 public task 全部 parse，平均 recall `0.6833`，平均 proxy score `0.6778`。低分样例的主要失败不是工具不可用，而是输出结构、字段语义、聚合口径和叙事文本解析策略出错。

## 错误行为汇总

### 1. 输出列结构判断错误

典型样例：`task_19`, `task_25`, `task_27`, `task_38`, `task_355`, `task_379`。

- 题目说 `full name`，但数据源是 `first_name` + `last_name` 两列时，错误地拼成 `full_name`，导致 gold 期望的原子列无法匹配。
- `Which event has the lowest cost?` 这种识别型问题只要输出 event 标识列，错误地额外输出 cost 列。
- `List all the withdrawals...` 这类问题通常要输出实体标识符或被问对象本身，错误地输出了多列明细。
- `tally X of each Y` 在没有 `count/number of/how many` 时更像逐项列举，错误地聚合成 `(element, count)`。

迁移规则：

- 最终输出列数必须先由题面直接询问的对象决定，不要把验证用的中间值也输出。
- 如果字段在数据源中被拆成原子列，优先保持原子列，不主动拼接。
- 如果知识文档明确要求两个字段一起使用，比如 `Always use both fields`，必须分列输出。
- `Which X has Y` 只输出 `X`；`What is Y of X` 只输出 `Y`；只有 `with/and their` 明确要求时才输出多个字段。

### 2. 自创列名和重算原始展示值

典型样例：`task_89`, `task_249`。

- 题目问 finish time，数据中已有 `time` 列，错误地从 `milliseconds` 重算成总用时。
- 输出列名自创为 `finish_time`, `avg_up_votes`，没有优先贴近原始字段名或题面完整措辞。

迁移规则：

- 如果问题概念能直接映射到原始字段，输出原始字段值，不要跨字段重算或改格式。
- 列名优先使用源字段名；计算字段才使用题面中的完整措辞，例如 `average_up_votes`，不要随意缩写。
- 时间、日期、编号等字段必须先看 `knowledge.md` 或 `meta` 的字段说明，区分展示字段和计算字段。

### 3. JSON/CSV 输出值类型和精度错误

典型样例：`task_169`, `task_200`, `task_344`, `task_418`。

- 数值型答案被输出为字符串，如 `"4"`、`"1"`、`"3"`。
- 平均值过早四舍五入，如 `82027220.30`，可能和 gold 的高精度数值不一致。

迁移规则：

- 最终 JSON 中 count、sum、avg、percentage 等数值必须用 JSON number，不要加引号。
- CSV 是脚本转换结果，但模型最终 JSON 仍应保留 number 类型。
- 平均值和比例不要过早截断；除非题目要求特定位数，否则保留足够精度。

### 4. 字段语义过度猜测

典型样例：`task_80`, `task_86`, `task_163`, `task_199`, `task_249`。

- `number` 在 F1 数据里可能是 car number、driver permanent number、race round 等，错误地凭直觉选择。
- `track number` 被误解为 round，没有充分验证是否应为 circuitId 或其他字段。
- `type of expenses` 被误解为明细 `expense_description`，实际更可能是预算层 `category`。
- `Riverside-related` 被过度扩展成 Riverside County 全部学校，实际更可能是名称包含 Riverside 的更小集合。
- StackOverflow 风格数据中 `posts` 可能含 question/answer，需要检查 `PostTypeId` 对口径的影响。

迁移规则：

- 遇到 `number/id/name/type/time/track/status/category` 这类通用词，必须先用 `find`、`meta`、`knowledge.md` 消歧。
- `X-related` 默认先取名称或字段中直接包含 `X` 的最小集合，再考虑行政区、上级区域或宽泛关联。
- 如果存在预聚合字段（如 `budget.spent`），先判断题目粒度是否停在预聚合层，不要无故展开明细重算。
- 多义字段不要只做一条查询；至少比较两种合理解释的结果规模和字段证据。

### 5. 聚合口径和缺失值规则错误

典型样例：`task_67`, `task_344`, `task_418`。

- `AVG(weight_kg)` 错误排除了 0；除非权威文档明确说 0 是缺失，否则标准聚合应保留 0，只排除 NULL/空值。
- 正常/异常阈值依赖临床或领域文本，错误地只用单一阈值或未确认边界是否包含。
- 行级记录和患者级实体混用，容易把“同一条记录同时满足条件”和“同一患者分别有记录满足条件”混为一谈。

迁移规则：

- 聚合前必须写清楚分母和去重粒度：行、实体、患者、事件、分子、账户等。
- 0 值默认是有效值；只有文档明确说 `0/NaN/-/blank` 代表缺失时才排除。
- 阈值题先找知识文档、字段 brief/detail、原文描述中的 normal range；不确定时做边界敏感性分析。
- 对“among A, how many have B”类题，先判断 A 和 B 是否必须在同一条记录上同时成立，还是同一实体任意记录成立。

### 6. 叙事文本解析策略不稳

典型样例：`task_396`, `task_418`。

- 长 Markdown 叙事文档中，正则反复调试耗时很高，还容易漏掉 `adjusted/corrected/verified from X to Y` 等修正语句。
- 没有充分利用 chunk、grep/read 行号和知识图谱实体来缩小范围。

迁移规则：

- 对叙事文档，先用 `grep` 定位章节，再用 `read` 读取行段；不要一开始就写全量正则。
- 对关键实体建立小表时，优先按段落和行号人工确认，再用 Python 做最后计算。
- 遇到修正语句，必须优先取最终值：`from old to new` 取 `new`；`corrected/revised/adjusted/verified` 后的最终陈述优先。
- 最终候选集要逐项回读原文验证，特别是极端题和医疗/化学/生物指标题。

### 7. 工具使用问题

典型样例：`task_38`, `task_67`, `task_86`, `task_418`。

- 过早使用 bash 全量扫描，绕过了 Pontis 的 `find/meta/chunk/pattern` 信息。
- 语义不确定时没有先用 `find(ref=..., query=...)` 检索图谱里的 lesson/pattern/字段摘要。

迁移规则：

- 开始时先 `find({"ref":"*:file"})`，再看 `knowledge.md`、README、chunk、pattern、列统计。
- JSON 用 `jd` 看结构；CSV/DB 先 `meta` 看列 brief/detail/sample/topk/null_count。
- `bash` 只做一次性精确计算或抽样验证，不能替代字段语义确认。
- 如果工具提示匹配多个实体，立刻改用完整 path-style ref。

## 建议注入测试脚本的提示词

下面这段可以作为 KDD test prompt 的“错误规避与输出审查”部分，放在最终输出约束和探索纪律之后。

```text
## Public Reflection 迁移规则

你必须吸收 public reflection 中的失败经验，最终输出前执行以下审查。

1. 输出列结构：
   - 先判断题目直接要求输出哪些字段，不要输出中间计算值或解释性辅助列。
   - "Which X has Y" 只输出 X；"What is Y of X" 只输出 Y；只有题目明确说 "with/and their" 才输出多个字段。
   - "full name"、地址、复合日期等概念如果在数据源中拆成原子字段，优先输出原子字段，不要自行拼接。
   - knowledge.md/schema 若要求字段分别使用，schema 指令优先于题面自然语言。

2. 列名和值：
   - 列名优先使用原始字段名；计算列使用题面完整措辞，不要随意缩写或发明同义词。
   - 如果源字段直接对应题目概念，输出源字段原始值，不要跨字段重算、翻译或重新格式化。
   - 时间、日期、编号、状态字段必须先读字段说明，区分展示值和计算值。

3. JSON 类型和精度：
   - count/sum/avg/percentage 等数值在最终 JSON rows 中必须是 number，不要加引号。
   - 不要过早四舍五入；除非题目要求位数，否则保留足够精度。
   - 空值按任务协议输出空字符串，但不要把真实数字变成字符串。

4. 语义消歧：
   - 遇到 number/id/name/type/time/track/status/category 等通用词，必须用 find/meta/knowledge.md 消歧。
   - 遇到 "X-related"，先尝试名称或字段中直接包含 X 的最小集合，再考虑区域/上级/宽泛关联。
   - 遇到预聚合字段时，先判断题目是否应在预聚合层回答，不要无故展开明细重算。
   - 多义字段至少比较两种合理解释的结果规模和证据，不要凭直觉选一个。

5. 聚合和缺失值：
   - 先明确统计粒度：行、实体、患者、事件、分子、账户等。
   - 0 默认是有效值；只有权威文档明确说 0 表示缺失才排除。
   - AVG 默认只排除 NULL/空值，不排除 0。
   - 阈值题必须查 normal range 和边界是否包含；不确定时做边界敏感性检查。
   - "Among A, how many have B" 必须判断 A 与 B 是否要求同一条记录同时满足，还是同一实体任意记录满足。

6. 叙事文本：
   - 长 Markdown 先 grep 定位章节，再 read 行段；不要一开始用全量正则硬抽。
   - 对修正语句取最终值：from old to new 取 new；corrected/revised/adjusted/verified 后的最终值优先。
   - 最终候选集必须逐项回读原文验证，尤其是医疗、化学、生物、叙事档案类任务。

7. 工具纪律：
   - 先用 Pontis 图谱：find、meta、chunk、pattern、jd、read。
   - bash 只用于只读精确计算或抽样验证，不能替代语义确认。
   - 如果 ref 匹配多个实体，立刻改用完整 path-style ref。

最终输出前，做一次自检：
- 列数是否只包含题目要求的字段？
- 复合字段是否应拆成原始列？
- 数值是否是 JSON number？
- 字段语义是否有 evidence 支持？
- 聚合粒度、缺失值、阈值边界是否已确认？
- 输出值是否能追溯到工具结果或原文行段？
```

## 需要优先修的系统层问题

1. 测试 prompt 当前说“列名不参与评分”，但 public proxy 实际会受列对齐影响。提示词应改为：列名不等于官方评分核心，但会影响本地 proxy 列匹配；应尽量贴近源字段或题面原词。
2. prompt 应显式要求 JSON 数值用 number 类型，避免 `["4"]` 这种字符串数值。
3. 对 full name、tally、which/list all、X-related、normal/abnormal、time/number 这些高频歧义词，应把上面的迁移规则作为强约束。
4. 叙事文本题需要更强的 chunk/read 流程约束，减少 bash 正则扫全文。
