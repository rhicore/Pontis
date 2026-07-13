# Spider 2.0 与 BIRD 三种结果评测的语义差异

## 结论

Spider 2.0-Snow/Lite、Pontis BIRD `strict_correct`、Pontis BIRD `business_correct` 都在执行 SQL 后比对结果，但它们定义的“结果正确”不是同一个谓词。不能把三者都概括为 execution match，更不能将它们的分数混排。

| 评测 | 判定的核心对象 | 核心问题 |
|---|---|---|
| Spider 2.0 focused evaluation | 逐列的答案值向量 | 题目真正关心的 answer fields 是否出现？ |
| BIRD strict | 完整行 tuple 的集合 | 预测是否返回与 gold 相同的答案关系？ |
| BIRD business | 完整行 tuple 的关系，加少量显式放宽 | 在保留答案关系的前提下，展示形式差异是否可接受？ |

最关键的边界是**行内字段关联**。Spider 2.0 focused evaluation 有意按列检查；BIRD strict/business 都比较整行 tuple，因此保留实体和属性之间的配对关系。

## 范围与来源

本文比较的是：

1. Spider 2.0-Snow/Lite 的 table-based execution evaluation。
2. Pontis BIRD 运行器输出的 `strict_correct`。
3. 同一运行器输出的 `business_correct`。

Spider 2.0-DBT 是 repository/code-agent task，使用 database/file-level script，不属于这里的 SQL 结果表直接比较。

Spider 2.0 的原始论文在 Appendix A 正式定义了 execution-based focused evaluation。当前本地 `data/Spider2` 与官方 `xlang-ai/Spider2` 的 `main` 分支在 2026-07-12 均为提交 `01a4c67c1e3f6ab9032716b050a927abbb245f65`；本文所述实现以该版本为准。

主要来源：

- [Spider 2.0 原始论文](<../../../Paper/src_files/Lei et al. - 2025 - Spider 2.0 Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows.pdf>)，正文的 annotation/evaluation 说明与 Appendix A。
- [Spider2-Snow 官方评测器](../../../data/Spider2/spider2-snow/evaluation_suite/evaluate.py)。
- [Spider2-Snow 逐题评测配置](../../../data/Spider2/spider2-snow/evaluation_suite/gold/spider2snow_eval.jsonl)。
- [Pontis BIRD 结果匹配器](../../scripts/BIRD/result_match.py)。

## 统一记号

令 gold 查询结果为关系 `G`，预测查询结果为关系 `P`。

- `rowset(R)`：关系 `R` 的行 tuple 集合。
- `col_i(R)`：`R` 的第 `i` 列构成的列向量。
- `K`：Spider 2.0 某题的 `condition_cols`；为空时表示所有 gold 列。
- `norm`：Pontis BIRD 的安全值规范化，包含有限浮点数精度、日期文本和字符串首尾空白处理。

三种评测的差异不在于是否执行 SQL，而在于执行之后对 `P` 与 `G` 使用什么等价关系。

## Spider 2.0: execution-based focused evaluation

### 论文的设计目标

Spider 2.0 论文认为企业数据任务的自然语言指令经常不完整规定最终 report 的全部展示列。若要求预测结果与 gold 表完全相同，模型可能回答了题目真正要求的指标，却因为额外展示列、列顺序或未被题面要求的背景列不同而被判错。

因此论文引入 execution-based focused evaluation：annotator 为每题写 evaluation script，以 `condition_cols` 指定真正需要检查的 gold 列；table-based 结果允许额外预测列。论文的示例是 Magnificent 7 的 year-to-date report，只检查 `Ticker` 和 `Change YTD` 两列，而不要求复现 gold report 的其他展示列。

论文用“降低 false negative、不增加 false positive”概括该经验性目标。这个主张针对的是不必要的输出列差异；论文没有针对多列 tuple 关联给出独立论证或实验。

### 形式化判定

论文 Appendix A 将一张结果表表述为列向量集合 `{v_i}`，并定义：只要 gold 的每个需要检查的列向量出现在预测输出中，即判正确。当前官方 `evaluate.py` 的表比较器等价于：

```text
SpiderFocused(P, G, K) = true
iff
  for every i in K (or every gold column when K is empty),
  there exists a prediction column j such that
  vector_match(col_i(G), col_j(P))
```

`vector_match` 的行为是：

- 两列长度必须相同。
- 数值使用绝对误差 `1e-2`。
- pandas 缺失值先归一为 `0`。
- `ignore_order=true` 时，两个列向量分别排序再比较。
- 不比较列名。
- 不要求不同 gold 列匹配到不同预测列。

这不是标准的关系表等价；它检查的是指定列的边缘值分布。

### 当前 Snow 配置事实

截至本文审计的本地官方版本：

| 项目 | 数量 |
|---|---:|
| 评测实例 | 547 |
| `ignore_order=true` | 547 |
| 显式设置非空 `condition_cols` | 293 |
| `condition_cols=[]`，检查全部 gold 列 | 254 |
| 带 `temporal` metadata | 37 |

评测器也支持一个题对应多个 gold CSV，命中任意一个均可得分。预测既可以以 SQL 提交、由 evaluator 在 live Snowflake 上执行，也可以直接以 CSV 提交。

### 它有意放宽了什么

若题目只要求若干 answer fields，Spider 2.0 可以接受：

```text
gold:      [Ticker, ChangeYTD, company_name, sector, ...]
prediction:[Ticker, ChangeYTD, explanation, debug_metric, ...]
```

只要 `Ticker` 和 `ChangeYTD` 对应的列向量通过 `condition_cols` 检查，其余列不影响得分。这正是 focused evaluation 的价值：对输出展示冗余、列位置和某些未规定 report 字段容忍度很高。

### 它不保证什么

对于多列复合答案，按列匹配不能保证 entity/value 配对。

```text
gold:
Ticker  ChangeYTD
AAPL    10
MSFT    20

prediction:
Ticker  ChangeYTD
AAPL    20
MSFT    10
```

在当前配置中 `ignore_order=true`，因此：

```text
col(Ticker)    = {AAPL, MSFT}
col(ChangeYTD) = {10, 20}
```

两列各自都匹配，Spider focused evaluation 会通过；但两个行 tuple 都是错误的：`(AAPL, 10)` 被改成 `(AAPL, 20)`，`(MSFT, 20)` 被改成 `(MSFT, 10)`。

因此它回答的是：

```text
每个关键字段的值是否出现？
```

而不是：

```text
关键字段是否共同构成正确的关系表？
```

这不是 OLAP 或 Snowflake 的数据模型要求。OLAP report 仍由行 tuple 构成；按列独立排序是 focused evaluator 为减少输出形式假阴性所作的评测取舍。它对单列答案、相互独立的值列表或只检查一个核心指标较合适；对 key-value、entity-metric、country-year-value 等复合 report 则可能产生语义 false positive。

### 论文论证的边界

论文提供了动机、形式化定义、一个 `condition_cols` 示例、人工审阅流程和泛称的 red-team 检查，但没有报告：

- `condition_cols` 的详细标注准则、annotator agreement 或逐类分布；
- focused evaluation 相对 tuple-level EX 的消融；
- false-positive/false-negative 的样本数、构造方式、混淆矩阵；
- entity/value 配对交换、join key 错配、grouping 错配等 adversarial 结果的通过率；
- 为什么多列 report 可以安全地从 tuple 关系降为独立列向量。

因此，论文的“不会增加 false positive”是经验性宣称，不能被解读为已证明 relation-level correctness。

## Pontis BIRD strict: tuple relation equality

Pontis BIRD runtime 在同一只读 SQLite 数据库上分别执行预测 SQL 和 gold SQL。严格判定为：

```text
BirdStrict(P, G) = true
iff rowset(P) == rowset(G)
```

其中一行是完整 tuple。例如 `(ticker, change_ytd)` 必须整体相同；不能把 `ticker` 与另一行的 `change_ytd` 重新配对。

| 属性 | BIRD strict 行为 |
|---|---|
| 行顺序 | 忽略 |
| 列顺序 | 不忽略 |
| 行内列关联 | 保留 |
| 多余预测列 | 不接受 |
| 遗漏 gold 列 | 不接受 |
| 列名/别名 | 不直接比较 |
| 重复完全相同行 | 当前 `frozenset` 实现忽略 multiplicity |
| 数值容差 | 无额外宽松；Python/SQLite 返回值需相等 |
| 空结果 | pred 与 gold 都为空时可接受 |
| 多个 gold | 当前不支持 |

它是关系语义上更干净的判定，但会将一些与题面无关的展示差异视为错误。例如 gold 只输出 `id`，预测输出 `(id, name)`，即使每个 id 都正确，也会 strict fail。

## Pontis BIRD business: 单一的 relation-preserving 判定

`business_correct` 使用完整关系记录，并保留重复次数：

```text
BirdBusiness(P, G, golden_sql) =
  same_width(P, G)
  AND global_column_mapping(P, G)
  AND (
    ordered_tuple_sequence_equal(P, G)       if outer_order_by(golden_sql)
    else tuple_bag_equal(P, G)
  )
```

### 当前规则

| match type | 判定 | 保留 tuple 关联？ |
|---|---|---|
| `exact` | 原始完整 row tuple 相等 | 是 |
| `value_equivalent` | 安全规范化后完整 tuple 相等 | 是 |
| `column_reorder` | 存在一个作用于所有行的全局列置换 | 是 |
| `row_bag_mismatch` | 无序 tuple 多重集合不同 | 是 |
| `ordered_row_mismatch` | golden 顶层有 `ORDER BY`，tuple 序列不同 | 是 |

这里的“全局”很重要。若 gold 是 `(Ticker, ChangeYTD)`，列重排必须在所有行上使用同一个映射。前述 AAPL/MSFT 交换数值的例子无法通过。预测多出或缺少任何列都会失败。

### 它有意不接受的差异

- 漏掉任何 gold 答案列；
- 选择了相同值域但不同字段角色的列；
- 破坏 key-value、entity-metric、parent-child 等行内关联；
- 改变 join 后行粒度、`DISTINCT` 口径、分母或聚合公式；
- 任意题目上的额外行，包括自动猜测的 top 边界并列行；
- 不同的表/字段/关系路径，仅因输出碰巧有部分相同值而接受。

`business_correct` 是 Pontis 的主业务指标，不是 BIRD 官方 leaderboard 指标。运行报告以它作为主 `correct/accuracy`，并保留 `strict_correct` 作为官方风格 EX 的诊断字段。`business_match_type` 只解释通过或失败原因，不构成第二个分数。

## 三种评测在同一反例上的结果

假设 Spider 的 `condition_cols=[0, 1]`，并以以下 gold 为目标：

```text
gold:
Ticker  ChangeYTD
AAPL    10
MSFT    20
```

| 预测结果 | Spider focused | BIRD strict | BIRD business | 原因 |
|---|---|---|---|---|
| 行顺序改为 `(MSFT,20),(AAPL,10)` | 通过 | 通过 | 视 gold SQL 而定 | gold 顶层无 `ORDER BY` 时忽略；有 `ORDER BY` 时失败。 |
| `(AAPL,20),(MSFT,10)` | 通过 | 失败 | 失败 | Spider 只见到两列边缘分布；BIRD 保留 tuple。 |
| `(AAPL,10,Apple),(MSFT,20,Microsoft)` | 通过 | 失败 | 失败 | Spider 可忽略额外列；business 要求输出列数等于 gold。 |
| 只输出 `Ticker` | 失败 | 失败 | 失败 | 当前 `condition_cols` 包含两个必要列；BIRD 也缺少 gold 列。 |
| Spider 仅要求 `Ticker` 时只输出 `Ticker` | 通过 | 失败 | 失败 | Spider 允许逐题声明 gold 的其他列不构成答案。 |
| top 题额外多一个完整 tuple 并列 | 无通用自动放宽 | 失败 | 失败 | 单一 golden 下不再根据问题关键词猜测并列语义。 |

## 为什么这三种分数不能混用

`SpiderFocused` 和 `BirdBusiness` 都会放宽结果形式，但放宽方向不同：

```text
SpiderFocused:
  允许删掉未指定 gold 字段；不要求 tuple 关联。

BirdBusiness:
  要求完整 gold tuple、列宽和重复次数一致；仅允许全局列重排与安全值规范化。
```

因此一个系统在 Spider focused EX 上得分更高，不代表它在完整 report、join role、entity-value 对齐或 BIRD business 上更正确。反过来，BIRD business 通过也不代表模型满足 Spider 某题人工指定的更窄 answer contract。

报告结果时应至少注明：

| 报告字段 | 必须说明 |
|---|---|
| `Spider2-Snow focused EX` | Spider2 版本、提交/数据日期、是否使用官方 `condition_cols`、是否 SQL 或 CSV 提交。 |
| `BIRD strict EX` | BIRD split、SQLite snapshot、是否忽略重复行 multiplicity。 |
| `BIRD business` | 它是 Pontis 诊断指标；同时给出 strict 分数和各 `match_type` 数量。 |

不要使用不带限定词的“execution accuracy”比较这三者。

## Pontis 最终采用的业务正确性指标

共享 evaluator 不要求 golden SQL，也不访问数据库。它接收一个 predicted result、一个或多个 golden result alternatives，以及 benchmark adapter 提供的排序和答案列策略。

唯一指标定义为：

```text
BusinessCorrect(P, [G1, G2, ..., Gn], policy) =
  RelationMatch(P, G1, policy_1)
  OR RelationMatch(P, G2, policy_2)
  OR ...
  OR RelationMatch(P, Gn, policy_n)
```

每次 `RelationMatch` 都要求一个全局列映射，并比较完整 tuple 序列或 tuple bag。不同 golden alternatives 之间不能混合列和行。

BIRD 每题仍然只有一个 golden SQL/result，不需要人为创建多个结果。BIRD adapter 的排序策略是：

1. 使用 `sqlglot` 解析 golden SQL。
2. 只检查最外层查询的 `ORDER BY`，不把子查询内部排序误认为最终输出顺序。
3. 有最外层 `ORDER BY` 时逐行比较完整 tuple 序列。
4. 没有时使用 `Counter(rows)` 比较完整 tuple 的无序多重集合。

列映射也只能是一个作用于全部记录的全局置换。它允许 `(ticker, value)` 与 `(value, ticker)` 这种纯展示顺序差异，但不允许逐列分别寻找相同值，更不允许交换不同实体对应的指标值。

例如 gold 为：

```text
(AAPL, 10)
(MSFT, 20)
```

预测 `(AAPL, 20), (MSFT, 10)` 必定失败。即使两列各自拥有相同的值集合，也不能破坏实体和值的行内关联。

### BIRD 列和值策略

- 预测列数必须与 gold 完全相同，禁止自动删除额外预测列。
- 允许一次作用于所有记录的全局列重排，当前最多支持 8 列结果。
- 字符串首尾空白、常见日期文本和有限浮点精度做保守规范化。
- `NULL` 保持独立，不等于 `0` 或空字符串。
- 空结果只有在预测和 gold 都为空且列宽相同时才通过。

`business_match_type` 的取值如下：

| 类型 | 含义 |
|---|---|
| `exact` | 原始完整记录相等 |
| `value_equivalent` | 安全规范化后完整记录相等 |
| `column_reorder` | 一次全局列重排后完整记录相等 |
| `projected_columns` | Spider 显式答案列契约允许忽略预测附加列后匹配 |
| `column_count_mismatch` | 预测与 gold 列数不同 |
| `row_count_mismatch` | 完整结果记录数量不同 |
| `row_bag_mismatch` | 无序完整记录多重集合不同 |
| `ordered_row_mismatch` | 有序完整记录序列不同 |
| `execution_error` | 任一 SQL 执行失败 |
| `missing_gold` | 没有 golden result |
| `no_gold_alternative_match` | 多个 golden alternatives 均未命中 |

以下 BIRD 旧放宽已经删除：

- 没有显式答案列契约时，将预测结果投影掉额外列后再与 gold 比较；
- 根据 top/highest/lowest 等问题关键词接受额外行；
- 使用 `frozenset` 忽略重复记录数量；
- Spider 2.0 式的独立列向量匹配。

默认 benchmark 和已有预测重评都以 `business_correct` 驱动主 `correct/accuracy`。`business_match_type` 只是失败原因或匹配方式，不是第二个指标。`strict_correct` 仅作为旧结果格式的兼容诊断字段保留。

### 使用方式

在 `Pontis/` 目录直接运行 benchmark：

```bash
uv run python scripts/BIRD/run_bird_benchmark.py --db california_schools
```

重评已有预测：

```bash
uv run python ../tools/evaluate_bird_predictions.py \
  --questions workspace/baselines/pontis/data/bird_dev/dev.json \
  --db-root workspace/baselines/pontis/data/bird_dev/dev_databases \
  --predictions path/to/results.jsonl \
  --prediction-format jsonl_rows \
  --output-dir path/to/evaluation_business
```

两个入口默认都是 `--eval-mode business`，摘要中的主 `correct/accuracy` 即业务正确率。

若只有已经导出的 CSV 或 JSON 结果，可以直接运行：

```bash
uv run python -m scripts.result_evaluation predicted.csv \
  --golden gold.csv
```

独立脚本默认按 bag 比较；明确需要逐行顺序一致时传入 `--ordered`。benchmark 不需要该参数，它会自动检查 golden SQL 最外层的 `ORDER BY`。

实现位置：

- [共享核心结果比较器](../../scripts/result_evaluation/core.py)
- [共享 CLI](../../scripts/result_evaluation/cli.py)
- [BIRD benchmark 接入](../../scripts/BIRD/bird_runner.py)
- [已有预测重评入口](../../../tools/evaluate_bird_predictions.py)

## 指标边界

该指标只能判断固定数据库快照上的业务输出是否正确。只有一个 golden result 时，无法识别“错误 SQL 恰好在当前数据上产生相同输出”的情况。这是单一结果提供的信息上限，不应通过逐列匹配、额外列投影或猜测并列语义来掩盖。

### 数据库访问边界

共享 evaluator 是静态评测器，不访问数据库、不执行 SQL，也不运行在线业务 assertion：

```text
Execution runner（可访问数据库）
  -> 执行 predicted/golden SQL
  -> 产出带列结构的 typed result

Result evaluator（不访问数据库）
  -> 比较 predicted result 与 golden alternatives
  -> 输出唯一 business_correct
```

结果文件足以判断完整 tuple、列映射、重复次数、顺序、数值容差和多个 alternatives。静态 evaluator 不负责判断：

- 错误 SQL 是否只是在当前数据上碰巧得到相同结果；
- golden 未收录时，Top-K 边界是否还有并列实体；
- 空结果是业务上确实无数据，还是错误过滤碰巧为空；
- Join 角色、时间快照或数据覆盖范围是否正确，但当前输出无法区分。

这些属于静态结果比较的信息边界，不通过查询数据库在 evaluator 内补救。SQL 执行和结果导出由 benchmark runner 在评测前完成。

## 没有 golden SQL、只有 golden result 时

共享评测器不依赖 golden SQL。golden SQL 只是在 BIRD 中用于生成 golden result，并提供最外层是否有 `ORDER BY` 的顺序信息。如果 benchmark 只发布结果文件，调用方直接传入 `ResultTable` 或 CSV/JSON 即可。

此时必须区分两个问题：

1. **结果内容**由 golden result 提供。
2. **结果是否有序**不能从 CSV 的当前排列可靠推断，必须由 benchmark 配置提供。

Spider2-Snow 已经提供 `ignore_order`，因此 adapter 使用：

```text
ordered = NOT ignore_order
```

如果一个新 benchmark 既没有 golden SQL，也没有顺序配置，共享 CLI 默认采用关系模型的无序 bag 语义。它不会根据 CSV 恰好采用的行顺序猜测业务排序要求。

## 多个 golden result

Spider 的 `_a.csv/_b.csv/...` 文件表示多个可接受答案。共享评测器把它们建模为 alternatives：

```text
BusinessCorrect(P, [G1, G2, ..., Gn]) =
  Match(P, G1) OR Match(P, G2) OR ... OR Match(P, Gn)
```

命中任意一个完整 alternative 即通过，并记录 `matched_gold_index` 和 `matched_gold_name`。以下做法不允许：

- 从 `G1` 取一列、从 `G2` 取另一列拼成答案；
- 从不同 golden 中分别挑选可匹配的行；
- 只要每个字段在某个 golden 出现过就通过。

Spider 的 `condition_cols` 可以为每个 golden alternative 指定必需答案列。共享 evaluator 会先选择该 alternative 的答案列，然后用一个全局预测列映射生成完整 tuple，再比较 tuple 序列或 tuple bag。因此它保留 Spider 的逐题答案契约，但不会继承官方逐列向量匹配导致的 entity/value 解耦问题。

Spider 当前发布数据存在两类配置数量不一致：

- `condition_cols` 多于 golden CSV 时，只消费与 CSV 数量对应的前几组。
- `condition_cols` 少于 golden CSV 时，未配置的 alternative 必须完整列匹配，避免猜测或复制其他结果的列契约。

## 共享实现

统一核心位于 [scripts/result_evaluation](../../scripts/result_evaluation/README.md)：

- [core.py](../../scripts/result_evaluation/core.py) 负责一个预测对单个或多个 golden alternatives 的比较。
- [BIRD adapter](../../scripts/BIRD/result_match.py) 提供 golden SQL 排序策略和旧字段兼容。
- [Spider adapter](../../scripts/spider/result_match.py) 读取 `condition_cols`、`ignore_order` 和多个 golden CSV。
- [Spider runner](../../scripts/spider/run_spider2_snow_benchmark.py) 在官方 Snowflake 执行后输出统一的 `business_evaluation/results.jsonl` 与 `results.json`。
