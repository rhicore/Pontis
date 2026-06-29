# Pontis Fact Investigation Validation 20260628

目标：让 Pontis 主调查报告和 explorer 写入的图谱元数据保持数据库事实口径，避免变成半个 solver。

## 当前设计

Pontis 事实层只表达这些内容：

- 数据库对象：db、table、col、file
- 字段边界：来源表、行粒度、覆盖范围、存储类别、值域、空值
- 值证据：sample、topk、cardinality、min/max、少量 query 验证
- 结构关系：fk、rel、overlap、disambig 的事实边界
- 事实缺口：图谱或局部验证尚未确认的数据库事实

事实层不承载查询处方、推荐字段、最终答案、候选 SQL sketch、输出列建议或 BIRD gold 风格。

## 已改动范围

- `Pontis/scripts/BIRD/investigation.py`
  - 调查 agent 只加载 `base/tool/guardrail/project`，不加载 SQL 规则和 README。
  - `query_mode=single_table_fact_check`，query 只做单表局部事实验证。
  - 报告结构改为 `# 数据库事实包`，包含 schema facts、similar field boundaries、metric inventory、value evidence、join/grain facts、open boundaries。
  - 输出后进入 report boundary validator，发现 solver-like 内容则阻断进入 SQL writer。

- `Pontis/explorer/*.py`
  - `schema_prepare`、`relation_disambiguation_review`、`disambiguate`、`readme`、`description_audit` 均改为数据字典/事实索引口径。
  - `rel` 写作稳定行级匹配事实；`disambig` 写作字段事实边界和值域边界。
  - 各 explorer 写完后调用 metadata tone normalizer。

- `Pontis/explorer/utils/metadata_tone.py`
  - 统一清理图谱写入中的误导性措辞，例如等价、同义、可替代、推荐使用、字段选择、SQL writer、SELECT/WHERE 片段。

- `Pontis/scripts/BIRD/extract.py`
  - extract 末尾统一 normalize graph metadata，并用 fact-boundary validator 检查图谱。
  - 检查发生在 embedding 之前，避免污染文本进入语义索引。

- `Pontis/scripts/BIRD/generate_investigation_reports.py`
  - 只生成 Pontis 数据库事实调查报告，不进入 SQL writer。
  - 每道题使用新的 investigation worker，避免跨题上下文污染。
  - 写出 Markdown 报告和 `summary.jsonl`，并立即运行 fact-boundary validator。

- `Pontis/tool/meta/tool.py`
  - disambig 邻接提示从“写 SQL 前必须读取”改为中性的“相关字段边界”。

- `Pontis/agent/prompt/_tool.py` 和 `Pontis/tool/query/tool.py`
  - query 工具说明改为数据库事实验证口径，避免“候选 SQL/答案行集合/题面条件”进入调查语境。

## 已验证

命令：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python -m py_compile \
  Pontis/agent/prompt/_tool.py \
  Pontis/agent/tools.py \
  Pontis/tool/meta/tool.py \
  Pontis/tool/query/tool.py \
  Pontis/explorer/schema_prepare.py \
  Pontis/explorer/relation_disambiguation_review.py \
  Pontis/explorer/disambiguate.py \
  Pontis/explorer/readme.py \
  Pontis/explorer/description_audit.py \
  Pontis/explorer/utils/metadata_tone.py \
  Pontis/scripts/BIRD/investigation.py \
  Pontis/scripts/BIRD/validate_fact_boundaries.py \
  Pontis/scripts/BIRD/extract.py \
  Pontis/scripts/BIRD/sql_writer_pipeline.py
```

命令：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/explorer/utils/test_metadata_tone.py
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/test_investigation_report.py
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/test_validate_fact_boundaries.py
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/test_fact_layer_prompts.py
```

命令：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/generate_investigation_reports.py \
  --qids 4 --workers 1 \
  --run-id fact_report_only_q4_gate_20260628 \
  --output-dir workspace/baselines/pontis/results/fact_report_only_q4_gate_20260628
```

结果：

```text
reports=1 boundary_failures=0
```

命令：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/generate_investigation_reports.py \
  --qids 4,53,80,81,83,344,350,849,851,879,928,984 \
  --workers 6 \
  --run-id fact_report_only_highrisk_20260628 \
  --output-dir workspace/baselines/pontis/results/fact_report_only_highrisk_20260628 \
  --allow-boundary-failures
```

第一轮结果：

```text
reports=12 boundary_failures=0
```

人工抽查发现第一轮 validator 漏掉了真实 solver 化内容：

- Q53: `查询逻辑为：...过滤...连接...求和`
- Q83: `COUNT(...)`
- Q879/Q984: `MAX(...)`

已修正：

- investigation report normalizer 删除 `查询逻辑`、`求和`、`聚合` 片段，并把 SQL aggregate 函数 token 改成事实性文字。
- fact-boundary validator 增加 `查询逻辑`、`求和`、`MAX(`、`MIN(`、`AVG(`、`COUNT(` 检查。
- `test_investigation_report.py` 和 `test_validate_fact_boundaries.py` 增加对应回归测试。

重跑失败子集：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/generate_investigation_reports.py \
  --qids 53,83,879,984 \
  --workers 4 \
  --run-id fact_report_only_highrisk_fix_20260628 \
  --output-dir workspace/baselines/pontis/results/fact_report_only_highrisk_fix_20260628 \
  --allow-boundary-failures
```

结果：

```text
reports=4 boundary_failures=0
```

组合验证：保留第一轮中已通过的 8 份报告，替换为修复后重跑的 4 份报告。

```text
checked_reports=12
OK
```

进一步人工扫描又发现以下流程化取数口径：

- Q81/Q344/Q350/Q984: `需从`、`要获取`、`以获取`、`再 join`
- Q350/Q984: `预期匹配条件`、`分组取 max 可定位`
- Q928: `明确要求返回 driverRef`、`可查询 driverRef`

已继续修正：

- normalizer 删除 `需从`、`要获取`、`以获取`、`再 join`、`预期匹配`、`可定位`、`可查询`、`分组取`、`要求返回` 片段。
- validator 增加同名检查项。
- `test_investigation_report.py` 和 `test_validate_fact_boundaries.py` 增加对应回归测试。

重跑相关子集：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/generate_investigation_reports.py \
  --qids 81,344,350,984 \
  --workers 4 \
  --run-id fact_report_only_procedure_fix_20260628 \
  --output-dir workspace/baselines/pontis/results/fact_report_only_procedure_fix_20260628 \
  --allow-boundary-failures

PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/generate_investigation_reports.py \
  --qids 350,984 \
  --workers 2 \
  --run-id fact_report_only_locating_fix_20260628 \
  --output-dir workspace/baselines/pontis/results/fact_report_only_locating_fix_20260628 \
  --allow-boundary-failures

PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/generate_investigation_reports.py \
  --qids 928 \
  --workers 1 \
  --run-id fact_report_only_q928_return_fix_20260628 \
  --output-dir workspace/baselines/pontis/results/fact_report_only_q928_return_fix_20260628 \
  --allow-boundary-failures
```

最终组合验证：以 `fact_report_only_highrisk_current_20260628` 为底，替换 Q81/Q344、Q350/Q984、Q928 为修复后报告。

```text
checked_reports=12
OK
```

人工扫描剩余命中只有 `meta 返回的...` 这种工具来源描述，没有发现 SQL sketch、取数流程、输出列处方或字段推荐。

命令：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/validate_fact_boundaries.py \
  --report-dir workspace/baselines/pontis/results/fact_report_only_smoke_20260628 \
  --report-glob '**/*.md'
```

结果：

```text
checked_reports=2 checked_graphs=0
OK
```

命令：

```bash
PYTHONPATH=tools:Pontis Pontis/.venv/bin/python Pontis/scripts/BIRD/validate_fact_boundaries.py \
  --report-dir workspace/baselines/pontis/results/fact_boundary_multidb_reports_20260628 \
  --report-dir workspace/baselines/pontis/results/fact_boundary_superhero_reports_20260628 \
  --report-dir workspace/baselines/pontis/results/fact_boundary_gate_smoke3_20260628 \
  --report-dir workspace/baselines/pontis/results/fact_boundary_gate_smoke4_20260628 \
  --report-dir workspace/baselines/pontis/results/fact_boundary_gate_smoke5_20260628 \
  --db california_schools,formula_1,card_games,superhero
```

结果：

```text
checked_reports=15 checked_graphs=4
OK
```

额外静态检查：

- `test_fact_layer_prompts.py` 覆盖 investigation prompt 渲染后不加载 SQL 规则和 README。
- `test_fact_layer_prompts.py` 覆盖 explorer base prompt 渲染后不包含 `候选过滤范围`、`候选计算值`、`字段选择`、`可替代`、`等价`、`SQL writer`、`下游 SQL`、`解题`、`答案` 等 solver 口径。
- `test_fact_layer_prompts.py` 覆盖 meta 工具的 disambig 邻接提示是“相关字段边界”，不再写“写 SQL 前必须读取”。

## 仍未证明

- 尚未对完整 BIRD dev 全库重新 extract 并验证全部图谱元数据。
- 尚未证明所有历史 context 中的报告都能通过新 boundary gate；当前只验证了 15 份新报告和 4 个已有图谱。
- SQL writer 的最终准确率不是本文档验证目标；本文档只记录 Pontis 事实调查层是否保持数据库事实口径。
