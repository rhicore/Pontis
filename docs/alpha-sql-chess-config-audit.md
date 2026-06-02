# Alpha-SQL、CHESS 与 DeepEye-SQL 运行配置调研

本文档对照当前论文实验所用配置与外部 baseline 原仓库配置，重点说明当前 BIRD dev 结果是否等价于官方最优配置。结论是：当前 Alpha-SQL、CHESS 与 DeepEye-SQL 结果均属于统一模型、统一评测脚本和资源约束下的本地复现配置，不应表述为官方最优配置或官方论文成绩。

## 调研范围

- 当前实验产物：
  - `workspace/baselines/alpha_sql/results/20260529_0545_deepeye_alpha_bird_dev_full_local_bge_speedmax_alpha_sql`
  - `workspace/baselines/chess/results/20260529_0525_chess_bird_dev_full_local_bge_small_forceprep_chess`
  - `workspace/baselines/deepeye_sql/results/20260529_0545_deepeye_alpha_bird_dev_full_local_bge_speedmax_deepeye_sql`
- 当前启动脚本：
  - `workspace/baselines/alpha_sql/slurm/20260529_0545_deepeye_alpha_bird_dev_full_local_bge_speedmax_alpha_sql.sh`
  - `workspace/baselines/chess/slurm/20260529_0525_chess_bird_dev_full_local_bge_small_forceprep_chess.sh`
  - `workspace/baselines/deepeye_sql/slurm/20260529_0545_deepeye_alpha_bird_dev_full_local_bge_speedmax_deepeye_sql.sh`
- 本地 wrapper：
  - `tools/run_alpha_sql_bird_dev.py`
  - `tools/run_chess_bird_dev.py`
  - `tools/run_deepeye_bird_dev.py`
- 上游原仓库配置：
  - Alpha-SQL: `git -C Alpha-SQL show HEAD:config/qwen32b_bird_dev.yaml`
  - CHESS: `git -C CHESS show HEAD:run/configs/CHESS_IR_CG_UT.yaml`
  - CHESS: `git -C CHESS show HEAD:run/configs/CHESS_IR_SS_CG.yaml`
  - DeepEye-SQL: `git -C DeepEye-SQL show HEAD:config/config-bird-example.toml`
  - DeepEye-SQL: `git -C DeepEye-SQL show HEAD:README.md`

注意：`Alpha-SQL`、`CHESS` 和 `DeepEye-SQL` 工作区均存在本地修改。本文档中的“官方配置”指外部仓库当前 HEAD 提交中的原始配置，而不是已经被本项目适配过的工作区文件。

## 总体结论

| 方法 | 当前论文结果 | 当前配置性质 | 官方配置差异 |
| --- | ---: | --- | --- |
| Alpha-SQL | 972 / 1534，63.36% | 大幅压缩搜索预算的快速零样本配置 | 官方 BIRD dev 配置使用 Qwen2.5-Coder-32B-Instruct、`max_rollout_steps=24`、`max_depth=16`、`n=3`，并保留动作级多候选与较高 validation 尝试次数 |
| CHESS | 900 / 1534，58.67% | `CHESS_IR_CG_ONLY_FAST`，只启用 Information Retriever 与 Candidate Generator | 官方脚本主要给出 `CHESS_IR_CG_UT` 和 `CHESS_IR_SS_CG`；前者包含 Unit Tester 且候选采样远高于当前配置，后者包含 Schema Selector |
| DeepEye-SQL | 1001 / 1534，65.25% | `--fast` 快速配置，禁用 few-shot ICL 并压缩各阶段采样预算 | 官方 README 报告 BIRD-Dev 73.5%，模型为 Qwen3-Coder-30B-A3B；原始 BIRD 配置保留 few-shot seeds、schema linking/generation/revision/selection 多采样预算 |

因此，论文中应继续强调这些 baseline 是“统一模型与统一评测条件下的本地复现配置”。尤其是 CHESS 的 58.67% 不代表 CHESS 原论文描述的完整框架或官方入口配置；Alpha-SQL 的 63.36% 不代表原始 MCTS 搜索预算下的结果；DeepEye-SQL 的 65.25% 也不代表其 README 中报告的 73.5% 官方公开输出。

## Alpha-SQL

### 当前运行配置

当前 Alpha-SQL 运行 ID 为：

```text
20260529_0545_deepeye_alpha_bird_dev_full_local_bge_speedmax_alpha_sql
```

评测结果来自 `evaluation.json`：

```text
correct = 972
total = 1534
accuracy = 63.36%
```

Slurm 脚本实际调用：

```bash
tools/run_alpha_sql_bird_dev.py \
  --preprocess-workers 48 \
  --mcts-workers 48 \
  --selection-workers 48 \
  --max-rollout-steps 1 \
  --max-depth 2 \
  --generation-n 1 \
  --revision-n 1 \
  --validation-max-tries 2 \
  --skip-preprocess
```

运行期生成的 `alpha_config.yaml` 中关键参数为：

```yaml
n_processes: 48
max_rollout_steps: 1
max_depth: 2
mcts_model_kwargs:
  model: deepseek-v4-flash
  n: 1
  top_p: 0.8
  max_tokens: 4096
  temperature: 0.8
  n_strategy: multiple
```

此外，本地 wrapper 还做了以下适配：

- 题目文件改为 zero-shot 版本，模型可见输入中移除 gold SQL。
- 模型统一读取 `global_config.py`，当前默认是 `deepseek-v4-flash`，OpenAI-compatible endpoint 为 DeepSeek API。
- embedding 强制使用本地 `BAAI/bge-small-en-v1.5`。
- `ALPHA_SQL_GENERATION_N=1`、`ALPHA_SQL_REVISION_N=1`、`ALPHA_SQL_VALIDATION_MAX_TRIES=2` 覆盖了 Alpha-SQL 动作生成默认值。
- `--skip-preprocess` 表示本次主运行直接复用已有预处理产物，不在该 Slurm 作业内重新跑预处理。

### 官方配置

上游 `config/qwen32b_bird_dev.yaml` 的原始关键配置为：

```yaml
n_processes: 32
max_rollout_steps: 24
max_depth: 16
mcts_model_kwargs:
  model: Qwen/Qwen2.5-Coder-32B-Instruct
  n: 3
  top_p: 0.8
  max_tokens: 4096
  temperature: 0.8
  n_strategy: single
```

上游 `alphasql/algorithm/mcts/mcts_action.py` 的动作级默认值为：

```python
SQL_GENERATION_LLM_KWARGS_N = 5
SQL_REVISION_LLM_KWARGS_N = 5
SQL_VALIDATION_MAX_TRIES = 15
```

官方脚本 `script/qwen32b_bird_dev_exp.sh` 直接运行：

```bash
python -m alphasql.runner.mcts_runner config/qwen32b_bird_dev.yaml
```

### 差异判断

当前 Alpha-SQL 与官方配置的差异主要不是并行度，而是搜索预算和模型配置：

- 搜索深度从 `16` 降为 `2`。
- rollout 步数从 `24` 降为 `1`。
- MCTS 模型从 `Qwen/Qwen2.5-Coder-32B-Instruct` 换成 `deepseek-v4-flash`。
- 单次 MCTS LLM 返回数从 `n=3` 降为 `n=1`。
- SQL generation/revision 采样从默认 `5/5` 降为 `1/1`。
- validation 最大尝试次数从 `15` 降为 `2`。

这些变化会显著减少 LLM 调用次数和运行时间，但也会削弱 Alpha-SQL 的 test-time search 能力。当前 63.36% 更适合作为“低搜索预算、统一模型条件下的 Alpha-SQL 复现结果”，不适合作为 Alpha-SQL 官方最佳性能的复现。

## CHESS

### 当前运行配置

当前 CHESS 运行 ID 为：

```text
20260529_0525_chess_bird_dev_full_local_bge_small_forceprep_chess
```

评测结果来自 `evaluation.json`：

```text
correct = 900
total = 1534
accuracy = 58.67%
```

Slurm 脚本实际调用：

```bash
tools/run_chess_bird_dev.py \
  --workers 8 \
  --config CHESS/run/configs/CHESS_IR_CG_ONLY_FAST.yaml \
  --llm-max-concurrent 8 \
  --llm-min-interval-seconds 0.5 \
  --llm-max-attempts 3 \
  --skip-preprocess
```

当前使用的 `CHESS_IR_CG_ONLY_FAST.yaml` 是本项目新增的快速配置，不属于 CHESS 上游原始 tracked 配置。关键参数为：

```yaml
setting_name: CHESS_IR_CG_ONLY_FAST

information_retriever:
  retrieve_context:
    top_k: 2

candidate_generator:
  generate_candidate:
    generator_configs:
      - template_name: generate_candidate_one
        sampling_count: 1
```

该配置只包含：

- Information Retriever：关键词抽取、entity retrieval、`top_k=2` 上下文检索。
- Candidate Generator：生成 1 个候选 SQL，并进行修正。

该配置不包含：

- Schema Selector。
- Unit Tester。
- 多候选 `generate_candidate_one` / `generate_candidate_two` 大规模采样。

本地 wrapper 还做了以下适配：

- 题目文件改为 zero-shot 版本，模型可见输入中移除 gold SQL。
- `CHESS/src/llm/engine_configs.py` 被本项目改为读取 `global_config.py`，因此配置文件中的 `gpt-4o-mini` / `gpt-4o` 名称会经由本地 OpenAI-compatible 参数实际指向统一模型，当前为 `deepseek-v4-flash`。
- embedding 强制使用本地 `BAAI/bge-small-en-v1.5`。
- 当前主运行使用 `--skip-preprocess`，复用已有 CHESS 预处理和 vector DB 产物。
- LLM 最大重试次数为 `3`，低于部分稳定复现实验脚本中使用的 `12`。

### 官方配置

CHESS 上游 README 描述的完整框架包含四类 agent：

- Information Retriever。
- Schema Selector。
- Candidate Generator。
- Unit Tester。

上游运行脚本给出两个主要入口：

```bash
sh run/run_main_ir_cg_ut.sh
sh run/run_main_ir_ss_cg.sh
```

其中 `run_main_ir_cg_ut.sh` 使用：

```yaml
config: ./run/configs/CHESS_IR_CG_UT.yaml
```

`CHESS_IR_CG_UT.yaml` 的关键参数为：

```yaml
retrieve_context:
  top_k: 5

generate_candidate:
  generator_configs:
    - template_name: generate_candidate_one
      sampling_count: 10
    - template_name: generate_candidate_two
      sampling_count: 10

unit_tester:
  generate_unit_test:
    unit_test_count: 20
    sampling_count: 1
```

`run_main_ir_ss_cg.sh` 使用：

```yaml
config: ./run/configs/CHESS_IR_SS_CG.yaml
```

`CHESS_IR_SS_CG.yaml` 包含 Schema Selector，并使用 `gpt-4o` 进行 table/column selection：

```yaml
schema_selector:
  select_tables:
    engine_config:
      engine_name: gpt-4o
  select_columns:
    engine_config:
      engine_name: gpt-4o
```

### 差异判断

当前 CHESS 与官方配置的差异主要包括：

- 当前配置只启用 IR + CG；官方完整框架还包含 SS 和/或 UT。
- context retrieval 从官方 `top_k=5` 降为 `top_k=2`。
- 候选生成从官方 `10 + 10` 个候选降为 `1` 个候选。
- 官方 `CHESS_IR_CG_UT` 包含 `20` 个自然语言 unit tests；当前配置没有 Unit Tester。
- 官方配置使用 `gpt-4o-mini` 和部分 `gpt-4o`；当前本地运行通过改造后的 engine layer 统一到 `deepseek-v4-flash`。

因此，当前 58.67% 不能解释为 CHESS 官方完整配置的性能。它更准确地表示：在统一模型、zero-shot、低预算快速配置下，只保留 IR + CG 主干时 CHESS 在 BIRD dev 上的表现。

## DeepEye-SQL

### 当前运行配置

当前 DeepEye-SQL 运行 ID 为：

```text
20260529_0545_deepeye_alpha_bird_dev_full_local_bge_speedmax_deepeye_sql
```

评测结果来自 `evaluation.json`：

```text
correct = 1001
total = 1534
accuracy = 65.25%
```

Slurm 脚本实际调用：

```bash
tools/run_deepeye_bird_dev.py \
  --parallel 48 \
  --internal-parallel 16 \
  --vector-db-parallel 24 \
  --fast
```

本次运行未使用 `--skip-preprocess`，因此包含以下阶段：

```text
preprocess_dataset
create_vector_db
value_retrieval
schema_linking
sql_generation
sql_revision
sql_selection
```

运行期生成的 `config-bird-zero-shot.toml` 中关键参数为：

```toml
[schema_linking]
direct_linking_sampling_budget = 1
reversed_linking_sampling_budget = 1

[sql_generation]
dc_sampling_budget = 1
skeleton_sampling_budget = 1
icl_sampling_budget = 0
icl_few_shot_examples_path = ""

[sql_revision]
checker_sampling_budget = 1

[sql_selection]
filter_top_k_sql = 1
evaluator_sampling_budget = 1
```

此外，本地 wrapper 和本地 DeepEye-SQL 代码做了以下适配：

- 题目文件保持 zero-shot 评测口径，模型可见输入中不使用 BIRD train 的 gold SQL、题目答案或 few-shot 示例。
- `--fast` 将 schema linking、SQL generation、SQL revision 和 SQL selection 的采样预算大幅降低。
- `icl_few_shot_examples_path` 被置空，`icl_sampling_budget=0`，因此关闭官方 BIRD few-shot seeds。
- 模型配置通过 `DeepEye-SQL/app/config/config.py` 读取项目根目录 `global_config.py`，当前实际统一到 `deepseek-v4-flash`。
- embedding 强制使用本地 `BAAI/bge-small-en-v1.5`，并使用 `local_index` 后端。
- 当前结果的在线效率为每题 5.339 轮 LLM 调用、12,637.4 uncached input tokens、5,430.2 output tokens；预处理 embedding tokens 为 13,641,276。

### 官方配置与公开结果

DeepEye-SQL 上游 README 报告的 BIRD-Dev 结果为：

```text
BIRD-Dev EX = 73.5
Model = Qwen3-Coder-30B-A3B
Public output = results/bird-dev/qwen3-coder-30b-a3b.json
```

上游 `config/config-bird-example.toml` 的原始关键配置为：

```toml
[schema_linking]
direct_linking_sampling_budget = 4
reversed_linking_sampling_budget = 4

[sql_generation]
dc_sampling_budget = 4
skeleton_sampling_budget = 4
icl_sampling_budget = 4
icl_few_shot_examples_path = "results/bird_dev_few_shots.json"

[sql_revision]
checker_sampling_budget = 3

[sql_selection]
filter_top_k_sql = 2
evaluator_sampling_budget = 5
```

官方通用脚本 `script/run_pipeline.sh` 按完整 pipeline 顺序执行预处理、vector DB 构建、value retrieval、schema linking、SQL generation、SQL revision 和 SQL selection。README 同时公开了 BIRD few-shot seeds，因此其 BIRD-Dev 73.5% 与当前本文 zero-shot 快速复现配置不属于同一设置。

### 差异判断

当前 DeepEye-SQL 与官方公开结果的差异主要包括：

- 模型从官方公开结果中的 `Qwen3-Coder-30B-A3B` 换成 `deepseek-v4-flash`。
- few-shot ICL 从官方配置中的 `results/bird_dev_few_shots.json` 改为完全关闭。
- direct/reversed schema linking 预算从 `4/4` 降为 `1/1`。
- SQL generation 中 direct candidate、skeleton、ICL 预算从 `4/4/4` 降为 `1/1/0`。
- SQL revision checker 预算从 `3` 降为 `1`。
- SQL selection 中候选保留和 evaluator 预算从 `filter_top_k_sql=2/evaluator_sampling_budget=5` 降为 `1/1`。
- embedding 后端统一为本地 BGE small，与官方公开输出的具体 embedding 环境不完全一致。

这些变化解释了当前 65.25% 与官方 README 73.5% 之间的差距。当前结果更适合作为“统一模型、zero-shot、低采样预算下的 DeepEye-SQL 本地复现结果”，不应写成 DeepEye-SQL 官方性能。

## 对论文表述的建议

论文中建议保持以下表述边界：

1. 主实验表可以继续报告当前五方法统一评测结果，但表注或实验设置中应说明 Alpha-SQL、CHESS 和 DeepEye-SQL 均为本地复现配置。
2. CHESS 的方法介绍可以先说明原始 CHESS 包含 IR、SS、CG、UT，再明确本实验采用 `CHESS_IR_CG_ONLY_FAST`，不代表完整 CHESS。
3. Alpha-SQL 的方法介绍可以说明原始方法依赖 MCTS test-time search，再明确本实验使用 `max_rollout_steps=1/max_depth=2` 的低搜索预算。
4. DeepEye-SQL 的方法介绍可以说明原始方法由 value retrieval、schema linking、SQL generation、SQL revision 和 SQL selection 组成，再明确本实验使用 `--fast`，关闭 few-shot ICL，并压缩各阶段采样预算。
5. 不应将当前 CHESS 58.67%、Alpha-SQL 63.36% 或 DeepEye-SQL 65.25% 与论文或排行榜中的官方最优成绩直接等价比较。
6. 若正文需要解释分数低于官方或预期，应归因于模型替换、few-shot/训练样例禁用、搜索/采样预算压缩、检索/验证阶段裁剪和本地 API 稳定性约束，而不是简单归因于方法本身无效。

可直接写入论文的表述示例：

```text
为保证不同方法在同一模型、同一 BIRD dev 划分和同一执行准确率脚本下可比较，本文对外部 baseline 采用本地复现配置。该配置移除了 BIRD train 中的 gold SQL、题目答案和 few-shot 示例，并将模型与 embedding 后端统一到本文实验环境。需要说明的是，Alpha-SQL、CHESS 与 DeepEye-SQL 的当前运行均采用低预算设置：Alpha-SQL 将 MCTS 搜索预算降为 max_rollout_steps=1、max_depth=2；CHESS 采用 CHESS_IR_CG_ONLY_FAST，仅保留 Information Retriever 与 Candidate Generator，不包含原始 CHESS 框架中的 Schema Selector 和 Unit Tester；DeepEye-SQL 使用 --fast 配置，关闭 few-shot ICL，并将 schema linking、SQL generation、SQL revision 和 SQL selection 的采样预算压缩到较低水平。因此，相关结果用于反映统一资源约束下的相对表现，而不等价于原论文或官方配置下的最佳成绩。
```

## 后续复现实验优先级

如果后续时间允许，建议按以下顺序补充：

1. CHESS：先跑 `CHESS_IR_CG_UT_BOUNDED` 或官方 `CHESS_IR_CG_UT` 的小规模抽样，估计 Unit Tester 和多候选采样带来的收益与成本。
2. Alpha-SQL：将 `max_rollout_steps/max_depth` 分别提高到 `4/4`、`8/8` 做预算曲线，不必一次性恢复到 `24/16`。
3. DeepEye-SQL：优先跑一个去掉 `--fast` 但仍保持 zero-shot 的小规模抽样，区分“few-shot 关闭”和“采样预算压缩”各自的影响。
4. 若需要接近官方成绩，再恢复官方模型族、few-shot seeds 和官方采样预算；这一步成本最高，且与本文“统一模型比较”的实验目标不同。
