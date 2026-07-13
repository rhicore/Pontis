# Shared SQL Result Evaluation

`scripts.result_evaluation` 是 BIRD 和 Spider 共用的 relation-safe 结果评测层。核心输入是一个预测结果、一个或多个 golden alternatives，以及由 benchmark adapter 提供的比较策略。

## 统一语义

```text
business_correct(P, [G1, G2, ...]) =
  compare_complete_relation(P, G1)
  OR compare_complete_relation(P, G2)
  OR ...
```

每个 alternative 内部始终比较完整 tuple。多个 golden result 表示多个可接受答案，不能把不同 golden 的列或行拼成一个答案。

顺序不是从 result 文件推断：

- BIRD adapter 从 golden SQL 最外层 `ORDER BY` 得到 `ordered`。
- Spider adapter 从 `spider2snow_eval.jsonl` 的 `ignore_order` 得到 `ordered`。
- 通用 CLI 默认把结果当作无序 bag，只有 `--ordered` 才按序列比较。

## 代码结构

- `core.py`：数据模型、单/多 golden 比较、完整 tuple、全局列映射、数值容差。
- `io.py`：CSV/JSON 结果加载。
- `cli.py`：独立命令行入口。
- `scripts/BIRD/result_match.py`：BIRD adapter。
- `scripts/spider/result_match.py`：Spider2-Snow adapter。

## CLI

一个 golden：

```bash
uv run python -m scripts.result_evaluation predicted.csv \
  --golden gold.csv
```

多个 golden alternatives：

```bash
uv run python -m scripts.result_evaluation predicted.csv \
  --golden gold_a.csv \
  --golden gold_b.csv
```

有序结果：

```bash
uv run python -m scripts.result_evaluation predicted.csv \
  --golden gold.csv \
  --ordered
```

Spider 风格的答案列可以按每个 golden 分别指定：

```bash
uv run python -m scripts.result_evaluation predicted.csv \
  --golden gold_a.csv --condition-cols 1,2 \
  --golden gold_b.csv --condition-cols 0 \
  --allow-extra-predicted-columns \
  --parse-numeric-strings \
  --numeric-tolerance 0.01
```

退出码为 `0` 表示业务正确，`1` 表示结果不匹配，`2` 表示输入或配置无效。
