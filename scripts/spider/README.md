# Spider2-Snow scripts

## Dev-only / gold-SQL subset

Spider2-Snow 本地完整题集在 `data/Spider2/spider2-snow/spider2-snow.jsonl`，但只有
`data/Spider2/spider2-snow/evaluation_suite/gold/sql/*.sql` 覆盖到的子集有 golden SQL。
这个子集适合用来调试 Pontis 的 SQL 正确性。

预处理只跑这个子集：

```bash
uv run python scripts/spider/extract.py --dev-only
```

benchmark 只跑这个子集：

```bash
uv run python scripts/spider/run_spider2_snow_benchmark.py --dev-only
```

`--gold-sql-only` 是 `--dev-only` 的别名。该选项可以继续和 `--db`、`--instances`、
`--limit` 组合使用，筛选顺序是先限制到 gold-SQL 子集，再应用这些过滤条件。
