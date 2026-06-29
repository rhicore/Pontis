# BIRD 宽松结果比对

BIRD 官方执行评测适合报告 benchmark 分数，但它会把一些对业务答案影响很小的输出形式差异判错。Pontis benchmark 现在保留严格分数，同时提供可选的 `business` 评测模式，用于诊断“答案事实是否已经包含在结果里”。

## 使用方式

运行 Pontis benchmark 时：

```bash
python3 scripts/BIRD/run_bird_benchmark.py --db california_schools --eval-mode business
```

重评已有预测时：

```bash
python3 tools/evaluate_bird_predictions.py \
  --questions workspace/baselines/pontis/data/bird_dev/dev.json \
  --db-root workspace/baselines/pontis/data/bird_dev/dev_databases \
  --predictions path/to/results.jsonl \
  --prediction-format jsonl_rows \
  --output-dir path/to/evaluation_business \
  --eval-mode business
```

默认仍是 `--eval-mode strict`。`business` 只影响输出摘要里的主 `correct/accuracy`，不会删除严格分数。

## 输出字段

- `strict_correct`：严格执行结果集合相等，和原 BIRD-style execution match 对齐。
- `business_correct`：宽松业务正确，包含下面列出的可接受差异。
- `relaxed_match_type`：本题命中的比对类型。

摘要里同时输出：

- `Strict`
- `Business Relaxed`
- `Relaxed Match Types`

## 当前放宽规则

严格匹配仍然先看原始执行结果集合：

```text
predicted.row_set == golden.row_set
```

当前实现继续忽略行顺序和重复行计数，这是原有执行集合比对已经具备的行为。

`business` 模式额外接受：

- `value_equivalent`：值规范化后一致。当前只做安全规范化：字符串首尾空白、常见日期格式、有限浮点数固定精度。
- `column_reorder`：预测列数与 gold 相同，只是列顺序不同。要求存在一个全局列重排后所有行集合一致。
- `predicted_superset`：预测多输出了列，但删除某些预测列后能和 gold 完全一致。
- `tie_superset`：top/highest/lowest/most/least 等问题中，预测结果包含 gold 行并额外返回并列候选。要求列宽相同，且预测行集合是真超集。

其他执行错误或结果不一致保持错误：

- `execution_error`
- `missing_gold`
- `no_match`

## 实现位置

核心比对逻辑在 [scripts/BIRD/result_match.py](/nfsdat2/home/bcchenslm/Projects/Text2SQL/Pontis/scripts/BIRD/result_match.py)。

Pontis benchmark runtime 在 [scripts/BIRD/benchmark_runtime.py](/nfsdat2/home/bcchenslm/Projects/Text2SQL/Pontis/scripts/BIRD/benchmark_runtime.py) 中保留列名和原始行：

```python
rows = tuple(tuple(row) for row in cursor.fetchall())
columns = tuple(item[0] for item in cursor.description or ())
return ExecutionResult(columns=columns, rows=rows)
```

[scripts/BIRD/run_bird_benchmark.py](/nfsdat2/home/bcchenslm/Projects/Text2SQL/Pontis/scripts/BIRD/run_bird_benchmark.py) 提供 `--eval-mode {strict,business}`。

[tools/evaluate_bird_predictions.py](/nfsdat2/home/bcchenslm/Projects/Text2SQL/tools/evaluate_bird_predictions.py) 使用同一套比对逻辑重评已有预测文件。
