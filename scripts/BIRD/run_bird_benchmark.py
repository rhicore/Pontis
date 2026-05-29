#!/usr/bin/env python3
"""BIRD Text-to-SQL Benchmark / Train Runner。

支持 dev 和 train 两种数据集：
  dev:   11 DB, 1534 queries
  train: 69 DB, 9428 queries (--train flag)

每个 query 生成一个主日志：
  q{id}.log        详细版（含每轮工具调用的完整参数和返回值）
启用 `--reflection` 时，错题还会额外生成：
  q{id}.reflection.log  题后复盘结果

常用命令：
    # dev 全量，本地直接跑。注意：如果 Neo4j 在 Slurm 节点上，应优先用 bird_slurm。
    python -m scripts.BIRD.run_bird_benchmark

    # train 全量。
    python -m scripts.BIRD.run_bird_benchmark --train

    # 单库 / 单题 / 小样本。
    python -m scripts.BIRD.run_bird_benchmark --db toxicology
    python -m scripts.BIRD.run_bird_benchmark --db toxicology --qids 1201,1202
    python -m scripts.BIRD.run_bird_benchmark --db toxicology --limit 10

    # 开错题 reflection。只对错题生成 q{id}.reflection.log。
    python -m scripts.BIRD.run_bird_benchmark --reflection

    # 关闭 BIRD 数据集级 README 注入，用于 ablation。
    python -m scripts.BIRD.run_bird_benchmark --no-bird-readme

    # 默认不使用 bird 全局经验库，只用当前数据库项目。
    python -m scripts.BIRD.run_bird_benchmark

    # 推荐 Slurm 入口：固定到 Neo4j 所在节点，避免 localhost:768x 连错机器。
    python -m scripts.BIRD.bird_slurm benchmark submit \
      --job-name bird-benchmark-dev \
      --cpus-per-task 24 --mem 128G --time 2-00:00:00 \
      -- \
      --db-workers 6 --workers 20 \
      --reflection \
      --run-id bird_dev_full_benchmark_YYYYmmdd

参数速查：
    数据范围：
      --train                 跑 train；默认跑 dev。
      --db A[,B]              只跑指定数据库，可逗号分隔。
      --qids 1,2,3            只跑指定 question_id。
      --limit N               每个数据库最多取前 N 题。

    并发：
      --db-workers N          并行数据库数。全 dev 常用 4-6。
      --workers N             每个数据库内并行 query 数。全 dev 常用 10-20。
                              总并发约为 db-workers * workers；过大可能被 LLM API、
                              Neo4j、日志 IO 或 guardrail 反复调用拖慢。

    Reflection：
      --reflection            每题验证后，只对错题做同会话复盘。
                              错题日志：q{id}.reflection.log。
                              复盘会看到 Result、Predicted SQL、Golden SQL、
                              predicted/golden execution result，并可继续调用工具核验数据库证据。
                              输出三分类：DB_EXPLORATION_FIXABLE、
                              DATASET_PRIOR_REQUIRED 或 GOLDEN_SQL_STYLE。
                              开启 --bird-readme 时，GOLDEN_SQL_STYLE 额外输出
                              README 覆盖性子类。

    bird 全局经验库：
      --use-bird-global       显式开启。会额外连接 `bird` project，检索 train example。
      --no-bird-global        显式关闭，只使用当前数据库 project。默认就是关闭。
      --clear-bird-knowledge  运行前清空 bird 中除 README 外的知识节点。
      --no-auto-sync-bird-global
                              bird 为空时不自动导入 train examples，直接失败。
      --no-bird-global-embedding
                              自动同步 bird 时不生成 embedding。
      --bird-train-json PATH  指定 bird 全局经验同步用的 train.json。

    Prompt：
      --prompt-profile full|minimal
                              full 是默认完整 prompt；minimal 只保留最小输出协议。
      --prompt-file PATH      用自定义主求解 prompt 模板。
      --no-bird-readme        不把 Pontis/scripts/BIRD/bird_readme.py 注入系统提示词。
                              同时关闭最终文本输出前的 BIRD README 复审 guardrail，
                              用于评估数据集级 SQL 写作逻辑的贡献。

    SQL 修复：
      --no-exec-repair        最终 SQL 执行失败时，不追加一次无工具修复。

    输出：
      --run-id ID             输出目录 ID。dev 日志写到
                              workspace/baselines/pontis/runtime_logs/YYYYmmdd_HHMMSS_bird_dev_<ID>/。
                              若 ID 已经以 YYYYmmdd_HHMMSS 开头，则直接使用该 ID。
      --output-dir PATH       结构化 results/evaluation 输出目录；默认在
                              workspace/baselines/pontis/results/YYYYmmdd_HHMMSS_bird_dev_<ID>/。

输出文件：
    progress.log              每库状态与总体进度。
    benchmark/q{id}.log       每题详细日志。
    benchmark/q{id}.reflection.log
                              错题复盘日志，仅 --reflection 且题目错误时生成。
    results/results.jsonl     每题结构化结果。
    results/predictions.json  question_id -> predicted SQL。
    evaluation/evaluation.json / summary.md
                              accuracy、token、rounds 等汇总。

指标含义：
    Accuracy                  SQL 执行结果与 golden SQL 执行结果集合相等的比例。
    Pre-input Tokens/Q        旧口径: 每题静态系统提示词/工具定义输入 token。
    Runtime Input Tokens/Q    旧口径: 每题非静态输入 token，包括历史工具结果等；不是商业 API cache miss。
    Cached Input Tokens/Q     provider 优先的 cache-hit/read input token；没有 provider 字段时用前缀估算。
    Uncached Input Tokens/Q   provider 优先的 cache-miss/write/regular input token；没有 provider 字段时用前缀估算。
    Runtime Output Tokens/Q   每题模型输出 token，包括工具调用参数、文本和最终 SQL。
    LLM Rounds/Q              每题串行 LLM 调用轮次。
    Total Tokens/Q            input + output 总 token，由 provider usage 汇总。
"""
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.BIRD.common import (
    PROJECT_ROOT,
    get_benchmark_dir,
    get_data_dir,
    get_db_base,
    get_progress_path,
    get_results_dir,
    get_run_id,
    get_run_name,
    set_run_id,
    PONTIS_WORKSPACE_ROOT,
)
from scripts.BIRD.bird_readme import build_bird_readme_system_prompt

logger = logging.getLogger(__name__)

# BIRD 求解阶段不使用通用 agent 默认配置。
# 这里显式声明脚本需要的工具、prompt 段和 guardrail，避免通用 agent 配置
# 被 benchmark 特例污染。
BIRD_BENCHMARK_TOOLS = ["find", "meta", "query"]
BIRD_BENCHMARK_PROMPTS = [
    "base", "tool", "ontology", "sql",
    "guardrail", "project", "readme", "effort",
]
BIRD_BENCHMARK_GUARDRAILS = [
    "round_limit", "exploration_check",
    "sql_check", "bridge_check", "disambig_check",
    "value_grounding_check",
    "bird_readme_final_recheck",
]

# ═══════════════════════════════════════════════════════════
#  Prompts
# ═══════════════════════════════════════════════════════════

# 主 benchmark 求解 prompt。
# 输入时机：
# - 每道题创建 agent 后
# - 在第一次 `agent.chat(prompt)` 时输入
# - 这是生成最终 SQL 之前的主用户提示词
QUERY_PROMPT_BASE_TEMPLATE = """\
你正在 BIRD 数据集的 benchmark 测试中。
{project_scope}

输出格式：一个 ```sql``` 代码块，代码块内是一条 SQLite SELECT 语句。多值答案用单列多行表示。
SELECT 输出列按问题文字顺序给出；题目列出多个地址字段时分别输出字段，不拼接成单个字符串。

{bird_global_section}

请根据以下信息生成一条 SQLite SQL 查询。

Question ID: {question_id}

问题：{question}

提示：{evidence}

"""

BIRD_PROJECT_SCOPE = """\
本次运行会打开两个 project：
- 当前数据库项目用于探索 schema、执行查询，并最终回答用户 query。
- `bird` 项目：BIRD 数据集的全局知识库，存储 SQL 生成任务的抽象知识和经验总结。
当前库 schema 以当前数据库项目为准；`bird` 提供跨库 SQL 经验参考。
项目 ref 入口：当前库 `{current_project}::*:file:db`，全局经验 `bird::*:example`。
"""

LOCAL_ONLY_PROJECT_SCOPE = """\
本次运行只打开当前数据库项目：`{current_project}`。
"""

BIRD_GLOBAL_PROMPT_SECTION = """\
关于 `bird` 经验的使用：
- 在 `bird` 项目的 example 知识中检索相近题型，迁移 SQL 写作风格和输出习惯。
- 检索词使用问题意图、SQL 形态、输出契约和 evidence 口径，例如 percentage、conditional aggregation、return id、multiply by 100。
- 最终 SQL 按当前数据库 schema 生成，并用检索到的 BIRD 偏好检查输出列、聚合粒度、排序、limit、distinct 和比例公式。

"""

QUERY_PROMPT_MINIMAL_TEMPLATE = """\
请根据以下 BIRD 问题生成一条 SQLite SQL 查询。
{project_scope}
{bird_global_section}

输出格式：一个 ```sql``` 代码块，代码块内是一条 SQLite SELECT 语句。
SELECT 输出列按问题文字顺序给出；题目列出多个地址字段时分别输出字段，不拼接成单个字符串。

Question ID: {question_id}

问题：{question}

提示：{evidence}

"""

PROMPT_PROFILES = ("full", "minimal")

# SQL 兜底 prompt。
# 输入时机：
# - 主求解阶段结束后，如果 agent 最后一轮回复里没有可解析的 SQL
# - 此时会临时禁用所有工具，再追加一次 `agent.chat(...)`
# - 目标是强制 agent 基于已有上下文直接收敛出最终 SQL
SQL_FALLBACK_PROMPT = """\
上一轮没有输出可解析的最终 SQL。

现在进入收敛阶段。基于当前对话里已经获得的 schema、样例、知识和查询结果，给出当前最佳 SQLite 查询。

输出格式：一个 ```sql``` 代码块，代码块内是一条 SELECT 语句。
"""

SQL_REPAIR_PROMPT_TEMPLATE = """\
当前最终 SQL 在 SQLite 中执行失败。

执行错误：
{error}

原 SQL：
```sql
{sql}
```

基于当前对话里已经获得的 schema、样例、知识和查询结果，给出修正后的 SQLite SELECT 查询。

输出格式：一个 ```sql``` 代码块，代码块内只放修正后的 SELECT 语句。
"""

REFLECTION_README_STYLE_REVIEW_SECTION = """\

README 覆盖性复盘要求：
- 本次运行启用了 BIRD README。若 `primary_error_category` 是 `GOLDEN_SQL_STYLE`，必须额外判断这个 style 失败与 README 的关系。
- `golden_sql_style_readme_subcategory` 必须且只能从以下三类中选择：
  - `README_STYLE_NOT_COVERED`：golden SQL 要求的风格/输出契约在 README 中没有提到，或者只有过于泛化的原则，无法指导模型稳定写中 golden。
  - `README_RULE_WRONG_OR_UNCLEAR`：README 提到了相关主题，但规则错误、互相冲突、边界不清，或遵守 README 后仍会合理地产生 predicted SQL 而不是 golden SQL。
  - `README_RULE_CLEAR_BUT_NOT_FOLLOWED`：README 已经清楚覆盖该风格要求，遵守它通常会写中 golden；错误主要是模型没有执行 README。
- 若 `primary_error_category` 不是 `GOLDEN_SQL_STYLE`，`golden_sql_style_readme_subcategory` 写 `not_applicable`。
- `readme_coverage_reason` 要引用 README 中是否覆盖相关风格，不要只重复 SQL 差异。
- `readme_minimum_update` 只在 README 缺失、错误或不清楚时提出最小补充/改写方向；README 已清楚覆盖时写 none。

Final README recheck 介入复盘要求：
- 主解题阶段可能启用了 `BirdReadmeFinalRecheck`。它不会调用额外审查流程，也不会读取示例；它会在最终 SQL 前通过 guardrail block 直接把完整 BIRD README 回灌给主 agent，要求主 agent 自查后只输出最终 SQL。
- 以 `Guardrail / blocks` 和详细执行轨迹为准判断 recheck 是否真正介入；出现 `BirdReadmeFinalRecheck` 的 block 就说明完整 README 已回灌给主 agent。
- 为兼容旧评测 schema，若 `primary_error_category` 是 `GOLDEN_SQL_STYLE`，必须额外输出 `style_reviewer_intervention`：
  - `REVIEWER_INTERVENED_BUT_NOT_FOLLOWED`：final README recheck 已经 BLOCK，README/checklist 若被执行通常会更接近 golden，但主 agent 最终没有执行或只部分执行。
  - `REVIEWER_INTERVENED_WITH_WRONG_ADVICE`：保留兼容字段；直接 README 模式下通常不要使用，除非 README/checklist 本身错误或明显误导了主 agent。
  - `REVIEWER_NOT_INTERVENED`：没有 `BirdReadmeFinalRecheck` block，或 README 回灌没有覆盖关键 style 问题。
- 若 `primary_error_category` 不是 `GOLDEN_SQL_STYLE`，`style_reviewer_intervention` 写 `not_applicable`。
"""

REFLECTION_NO_README_STYLE_REVIEW_SECTION = """\

README 覆盖性复盘要求：
- 本次运行未启用 BIRD README。
- `golden_sql_style_readme_subcategory` 固定写 `not_applicable`。
- `readme_coverage_reason` 和 `readme_minimum_update` 固定写 none。

Final README recheck 介入复盘要求：
- 主解题阶段可能启用了 `BirdReadmeFinalRecheck`。它不会调用额外审查流程，也不会读取示例；它会在最终 SQL 前通过 guardrail block 直接把完整 BIRD README 回灌给主 agent。
- 以 `Guardrail / blocks` 和详细执行轨迹为准判断它是否真正介入；没有 `BirdReadmeFinalRecheck` block，或 README 回灌没有覆盖关键 style 问题，归为 `REVIEWER_NOT_INTERVENED`。
- 若 `primary_error_category` 是 `GOLDEN_SQL_STYLE`，输出 `style_reviewer_intervention`：
  - `REVIEWER_INTERVENED_BUT_NOT_FOLLOWED`
  - `REVIEWER_INTERVENED_WITH_WRONG_ADVICE`
  - `REVIEWER_NOT_INTERVENED`
- 若 `primary_error_category` 不是 `GOLDEN_SQL_STYLE`，`style_reviewer_intervention` 写 `not_applicable`。
"""

# 题后反思 prompt。
# 输入时机：
# - 只有开启 `--reflection` 时才会使用
# - 每道错题完成、SQL 已执行并得到 wrong / error 结果之后输入
# - 复用同一个 agent 会话，让 agent 基于刚才的执行轨迹继续调用工具核验数据库证据
REFLECTION_CASE_PROMPT_TEMPLATE = """\
你现在仍在同一个对话上下文里：刚刚的 benchmark 消息、工具调用和最终 SQL 都还在。
你不是新开一个会话，而是继续复盘这条已经完成并已验证结果的 benchmark case。

按 benchmark 的解题工作流回放这道错题，判断做错的根因。你拥有正常 agent 的工具权限；
不要只根据下面的文本包下结论，必须主动调用工具重新核验 predicted SQL 和 golden SQL 涉及的
表、列、值、连接路径、行粒度和关键中间结果。
golden SQL 在本阶段是有意提供给你的：把它当成待验证假设的来源，沿着它使用的表、列、值、
连接路径、行粒度和中间结果做定向探索，再与 predicted SQL 的假设逐项对比。不要在未确认
当前数据库是否支持 golden interpretation 之前完成分类。

本轮复盘对象：
- 数据库项目：{db_id}
- Question ID: {question_id}
- Difficulty: {difficulty}
- Result: {result}
- Elapsed: {elapsed:.1f}s

题目：
{question}

Evidence：
{evidence}

Predicted SQL：
{predicted_sql}

Golden SQL：
{golden_sql}

Benchmark 调用链摘要：
{calls_summary}

Guardrail / blocks：
{blocks_summary}

Predicted execution result：
{predicted_execution}

Golden execution result：
{golden_execution}

详细执行轨迹：
{trace_detail}

你的任务：
1. 先用工具做数据库证据审计：查询 predicted SQL 与 golden SQL 的关键中间集合，检查 schema/meta/样例值/连接路径/行粒度。必要时把两个 SQL 拆成更小的 COUNT、DISTINCT、GROUP BY、JOIN 覆盖率或样例行查询。
2. 如果启用了 `bird` 全局项目，可以检索相关经验辅助解释，但不能用其他题的 golden SQL 偏好替代当前数据库证据。
3. 最终只输出复盘结论，不输出长篇工具过程。

错误三分类要求：
- 必须把主因归入且只归入以下三类之一：`DB_EXPLORATION_FIXABLE`、`DATASET_PRIOR_REQUIRED`、`GOLDEN_SQL_STYLE`。

分类测试：
1. 先判断 predicted SQL 和 golden SQL 是在“数据库语义”上不一致，还是只在“答案呈现/SQL 表达”上不一致。
2. 如果二者使用的数据库实体和值大体相同，差异主要是输出形状、聚合呈现、DISTINCT、分组、排序、LIMIT/tie、NULL 处理、重复行、舍入/格式或 SQLite 表达方式，选 `GOLDEN_SQL_STYLE`。
3. 否则这是数据库理解错误。接着做 database-only oracle test：
   - 假设一个 oracle 只能读取当前 question、evidence、schema、完整数据库内容和当前项目图谱/文档。
   - oracle 不能读取其他 query-SQL pair、benchmark 历史、训练样例或隐藏 golden 风格。
   - 如果这个 oracle 能找到当前数据库中的具体证据，唯一排除 predicted interpretation 并支持 golden interpretation，选 `DB_EXPLORATION_FIXABLE`。
   - 如果这个 oracle 不能唯一决定，golden 的选择依赖同库 query log、业务约定、benchmark 约定、命名先验或跨题经验，选 `DATASET_PRIOR_REQUIRED`。

类别定义：
- `DB_EXPLORATION_FIXABLE`：当前数据库信息足够；错误应该能通过更充分数据库探索或更好的 schema/value 标注修正。
- `DATASET_PRIOR_REQUIRED`：当前数据库信息不足以唯一决定；错误需要 query-log 记忆、benchmark 约定、业务先验、命名先验或跨题经验修正。
- `GOLDEN_SQL_STYLE`：数据库理解基本正确；错误在目标 SQL 风格或结果形状。
{readme_style_review_section}

硬边界：
- 不要用“是否要改表/列/JOIN”区分前两类；`DB_EXPLORATION_FIXABLE` 和 `DATASET_PRIOR_REQUIRED` 都可能需要改表、列、值或连接。
- `DB_EXPLORATION_FIXABLE` 必须在 `decisive_db_evidence` 中引用具体当前数据库事实，例如字段含义、样例值、枚举覆盖、主外键路径、行粒度、JOIN 覆盖率、中间查询结果或某个候选路径会错误增删行的证据。
- 如果 `decisive_db_evidence` 只能写得很空泛、缺失，或者只是“golden SQL 使用了另一个表/列”，不要选 `DB_EXPLORATION_FIXABLE`，应选 `DATASET_PRIOR_REQUIRED`。

最终复盘文本必须包含以下字段：
primary_error_category: DB_EXPLORATION_FIXABLE | DATASET_PRIOR_REQUIRED | GOLDEN_SQL_STYLE
database_only_oracle_verdict: yes_unique_db_evidence | no_needs_prior | not_applicable_style
decisive_db_evidence: 当前数据库中支持分类的具体证据；若不是 DB_EXPLORATION_FIXABLE，写 none
plausible_alternatives: 若是 DATASET_PRIOR_REQUIRED，列出 predicted 与 golden 各自为何都可解释；否则写 none
missing_prior: 若是 DATASET_PRIOR_REQUIRED，说明需要哪类 query log / 业务口径 / benchmark 先验；否则写 none
mistake_summary: 一句话总结错误
minimum_fix: 最小修正方向
classification_reason: 为什么该错因属于上面的唯一类别
golden_sql_style_readme_subcategory: README_STYLE_NOT_COVERED | README_RULE_WRONG_OR_UNCLEAR | README_RULE_CLEAR_BUT_NOT_FOLLOWED | not_applicable
readme_coverage_reason: 若是 GOLDEN_SQL_STYLE 且启用 README，说明 README 是否覆盖该风格；否则写 none
readme_minimum_update: 若 README 缺失、错误或不清楚，写最小更新方向；否则写 none
style_reviewer_intervention: REVIEWER_INTERVENED_BUT_NOT_FOLLOWED | REVIEWER_INTERVENED_WITH_WRONG_ADVICE | REVIEWER_NOT_INTERVENED | not_applicable
"""

REFLECTION_CASE_NO_BIRD_PROMPT_TEMPLATE = """\
你现在仍在同一个对话上下文里：刚刚的 benchmark 消息、工具调用和最终 SQL 都还在。
你不是新开一个会话，而是继续复盘这条已经完成并已验证结果的 benchmark case。

本次运行使用当前数据库项目、工具调用轨迹、预测 SQL 和 golden SQL 做复盘，输出高密度错误归因与可复用改进建议。
你拥有正常 agent 的工具权限；不要只根据下面的文本包下结论，必须主动调用工具重新核验
predicted SQL 和 golden SQL 涉及的表、列、值、连接路径、行粒度和关键中间结果。
golden SQL 在本阶段是有意提供给你的：把它当成待验证假设的来源，沿着它使用的表、列、值、
连接路径、行粒度和中间结果做定向探索，再与 predicted SQL 的假设逐项对比。不要在未确认
当前数据库是否支持 golden interpretation 之前完成分类。

本轮复盘对象：
- 数据库项目：{db_id}
- Question ID: {question_id}
- Difficulty: {difficulty}
- Result: {result}
- Elapsed: {elapsed:.1f}s

题目：
{question}

Evidence：
{evidence}

Predicted SQL：
{predicted_sql}

Golden SQL：
{golden_sql}

Benchmark 调用链摘要：
{calls_summary}

Guardrail / blocks：
{blocks_summary}

Predicted execution result：
{predicted_execution}

Golden execution result：
{golden_execution}

详细执行轨迹：
{trace_detail}

你的任务：
1. 先用工具做数据库证据审计：查询 predicted SQL 与 golden SQL 的关键中间集合，检查 schema/meta/样例值/连接路径/行粒度。必要时把两个 SQL 拆成更小的 COUNT、DISTINCT、GROUP BY、JOIN 覆盖率或样例行查询。
2. 判断当前错误属于哪一种信息来源问题，而不是简单判断 SQL 文本差异。
3. 输出只包含复盘结论，不输出长篇工具过程。

错误三分类要求：
- 必须把主因归入且只归入以下三类之一：`DB_EXPLORATION_FIXABLE`、`DATASET_PRIOR_REQUIRED`、`GOLDEN_SQL_STYLE`。

分类测试：
1. 先判断 predicted SQL 和 golden SQL 是在“数据库语义”上不一致，还是只在“答案呈现/SQL 表达”上不一致。
2. 如果二者使用的数据库实体和值大体相同，差异主要是输出形状、聚合呈现、DISTINCT、分组、排序、LIMIT/tie、NULL 处理、重复行、舍入/格式或 SQLite 表达方式，选 `GOLDEN_SQL_STYLE`。
3. 否则这是数据库理解错误。接着做 database-only oracle test：
   - 假设一个 oracle 只能读取当前 question、evidence、schema、完整数据库内容和当前项目图谱/文档。
   - oracle 不能读取其他 query-SQL pair、benchmark 历史、训练样例或隐藏 golden 风格。
   - 如果这个 oracle 能找到当前数据库中的具体证据，唯一排除 predicted interpretation 并支持 golden interpretation，选 `DB_EXPLORATION_FIXABLE`。
   - 如果这个 oracle 不能唯一决定，golden 的选择依赖同库 query log、业务约定、benchmark 约定、命名先验或跨题经验，选 `DATASET_PRIOR_REQUIRED`。

类别定义：
- `DB_EXPLORATION_FIXABLE`：当前数据库信息足够；错误应该能通过更充分数据库探索或更好的 schema/value 标注修正。
- `DATASET_PRIOR_REQUIRED`：当前数据库信息不足以唯一决定；错误需要 query-log 记忆、benchmark 约定、业务先验、命名先验或跨题经验修正。
- `GOLDEN_SQL_STYLE`：数据库理解基本正确；错误在目标 SQL 风格或结果形状。
{readme_style_review_section}

硬边界：
- 不要用“是否要改表/列/JOIN”区分前两类；`DB_EXPLORATION_FIXABLE` 和 `DATASET_PRIOR_REQUIRED` 都可能需要改表、列、值或连接。
- `DB_EXPLORATION_FIXABLE` 必须在 `decisive_db_evidence` 中引用具体当前数据库事实，例如字段含义、样例值、枚举覆盖、主外键路径、行粒度、JOIN 覆盖率、中间查询结果或某个候选路径会错误增删行的证据。
- 如果 `decisive_db_evidence` 只能写得很空泛、缺失，或者只是“golden SQL 使用了另一个表/列”，不要选 `DB_EXPLORATION_FIXABLE`，应选 `DATASET_PRIOR_REQUIRED`。

最终复盘文本必须包含以下字段：
primary_error_category: DB_EXPLORATION_FIXABLE | DATASET_PRIOR_REQUIRED | GOLDEN_SQL_STYLE
database_only_oracle_verdict: yes_unique_db_evidence | no_needs_prior | not_applicable_style
decisive_db_evidence: 当前数据库中支持分类的具体证据；若不是 DB_EXPLORATION_FIXABLE，写 none
plausible_alternatives: 若是 DATASET_PRIOR_REQUIRED，列出 predicted 与 golden 各自为何都可解释；否则写 none
missing_prior: 若是 DATASET_PRIOR_REQUIRED，说明需要哪类 query log / 业务口径 / benchmark 先验；否则写 none
mistake_summary: 一句话总结错误
minimum_fix: 最小修正方向
classification_reason: 为什么该错因属于上面的唯一类别
golden_sql_style_readme_subcategory: README_STYLE_NOT_COVERED | README_RULE_WRONG_OR_UNCLEAR | README_RULE_CLEAR_BUT_NOT_FOLLOWED | not_applicable
readme_coverage_reason: 若是 GOLDEN_SQL_STYLE 且启用 README，说明 README 是否覆盖该风格；否则写 none
readme_minimum_update: 若 README 缺失、错误或不清楚，写最小更新方向；否则写 none
style_reviewer_intervention: REVIEWER_INTERVENED_BUT_NOT_FOLLOWED | REVIEWER_INTERVENED_WITH_WRONG_ADVICE | REVIEWER_NOT_INTERVENED | not_applicable
"""

DB_EXTS = (".sqlite", ".db", ".sqlite3", ".duckdb")

def assign_question_ids(questions: list[dict]) -> list[dict]:
    """为没有 question_id 的数据集补一个稳定 id。"""
    normalized = []
    for idx, q in enumerate(questions):
        item = dict(q)
        if item.get("question_id") is None:
            item["question_id"] = idx
        normalized.append(item)
    return normalized

# ═══════════════════════════════════════════════════════════
#  SQL 提取与执行
# ═══════════════════════════════════════════════════════════

_SQL_BLOCK_RE = re.compile(r"```sql\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_SELECT_RE = re.compile(r"(SELECT\s.+?)(?:;|$)", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str | None:
    if not text:
        return None
    blocks = _SQL_BLOCK_RE.findall(text)
    if blocks:
        sql = blocks[-1].strip()
        if sql:
            return sql
    matches = _SELECT_RE.findall(text)
    if matches:
        return matches[-1].strip()
    return None


def execute_sql(db_path: str, sql: str) -> set | str:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        conn.close()
        return set(tuple(r) for r in rows)
    except Exception as e:
        return f"ERROR: {e}"


def is_correct(predicted: set | str, golden: set | str) -> bool:
    if isinstance(predicted, str) or isinstance(golden, str):
        return False
    return predicted == golden


def format_execution_result(result: set | str, limit: int = 20) -> str:
    """Compact execution result for reflection prompts."""
    if isinstance(result, str):
        return result
    rows = sorted(result, key=lambda row: tuple(str(item) for item in row))
    shown = rows[:limit]
    text = json.dumps(shown, ensure_ascii=False, default=str)
    if len(rows) > limit:
        text += f"\n... ({len(rows) - limit} more rows; total {len(rows)})"
    else:
        text += f"\n(total {len(rows)})"
    return text


# ═══════════════════════════════════════════════════════════
#  Trace 收集 + 两级日志
# ═══════════════════════════════════════════════════════════

class TraceCollector:
    """收集 agent 事件，生成简洁版和详细版日志。"""

    def __init__(self):
        self._next_round = 1
        self._entries = []  # [{type, round, ...}]
        self._pending_by_id = {}

    def callback(self, event: dict):
        etype = event.get("type")

        if etype == "tool_call":
            entry = {
                "type": "call",
                "round": self._next_round,
                "name": event["name"],
                "args": event.get("arguments", {}),
                "result": None,
            }
            self._entries.append(entry)
            if event.get("id"):
                self._pending_by_id[event["id"]] = entry
            self._next_round += 1
        elif etype == "tool_result":
            result = event.get("result", "")
            entry = None
            event_id = event.get("id")
            if event_id:
                entry = self._pending_by_id.pop(event_id, None)
            if entry is None:
                for item in reversed(self._entries):
                    if (
                        item["type"] == "call"
                        and item["name"] == event.get("name")
                        and item["result"] is None
                    ):
                        entry = item
                        break
            if entry is not None:
                entry["result"] = result
        elif etype == "blocked":
            self._entries.append({
                "type": "block",
                "round": self._next_round,
                "source": event.get("guardrail", ""),
                "msg": event.get("content", ""),
                "name": event.get("name"),
                "args": event.get("arguments", {}),
            })
            self._next_round += 1
        elif etype in {"warning", "sidechain", "append", "trace"}:
            self._entries.append({
                "type": etype,
                "round": self._next_round,
                "source": event.get("guardrail", ""),
                "msg": event.get("content", ""),
                "name": event.get("name"),
                "args": event.get("arguments", {}),
                "call_index": event.get("call_index"),
                "trace_only": bool(event.get("trace_only")),
            })
        elif etype == "done":
            self._pending_by_id.clear()

    def write_logs(self, bench_dir: Path, qid: int, q: dict,
                   response: str, predicted_sql: str | None,
                   result_str: str, elapsed: float,
                   efficiency: dict | None = None):
        """写两个日志文件。"""
        efficiency = efficiency or empty_efficiency_metrics()
        # ── 通用头部 ──
        header = "\n".join([
            f"Q{qid} [{q.get('difficulty', '?')}] {result_str} {elapsed:.1f}s",
            f"Question: {q['question']}",
            f"Evidence: {q.get('evidence', '') or '(无)'}",
            f"Predicted SQL: {predicted_sql or 'PARSE_ERROR'}",
            f"Golden SQL: {q['SQL']}",
            (
                "LLM Efficiency: "
                f"rounds={efficiency.get('llm_rounds', 0)}, "
                f"cached_input_tokens={efficiency.get('cached_input_tokens', 0)}, "
                f"uncached_input_tokens={efficiency.get('uncached_input_tokens', 0)}, "
                f"output_tokens={efficiency.get('output_tokens', 0)}, "
                f"total_tokens={efficiency.get('total_tokens', 0)}"
            ),
        ])

        # ── 详细版 ──
        detail_lines = [header, "---"]
        for entry in self._entries:
            if entry["type"] == "call":
                args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
                detail_lines.append(f"Round {entry['round']} | {entry['name']}({args_full})")
                result = entry["result"] or "(no result)"
                detail_lines.append(f"  {result}")
            else:
                detail_lines.append(self._format_event_header(entry))
                detail_lines.append(f"  {_format_event_message(entry)}")
            detail_lines.append("---")

        if response:
            detail_lines.append(f"Agent response:\n{response[-1000:]}")
        detail_lines.append("")
        (bench_dir / f"q{qid}.log").write_text("\n".join(detail_lines), encoding="utf-8")

    def summarize_calls(self) -> str:
        parts = []
        for entry in self._entries:
            if entry["type"] == "call":
                parts.append(f"{entry['name']}({_args_brief(entry['args'])})")
            elif entry.get("name"):
                parts.append(f"{entry['name']}({_args_brief(entry['args'])})(blocked)")
        return " → ".join(parts) if parts else "(no calls)"

    def summarize_blocks(self) -> str:
        parts = []
        for entry in self._entries:
            if entry["type"] != "block":
                continue
            label = f"{entry['name']}({_args_brief(entry['args'])})" if entry.get("name") else "text response"
            msg = _normalize_block_message(entry["msg"])
            parts.append(f"[{entry['source']}] {label}: {msg}")
        return "\n".join(parts) if parts else "(none)"

    def detailed_trace_text(self) -> str:
        lines = []
        for entry in self._entries:
            if entry["type"] == "call":
                args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
                lines.append(f"Round {entry['round']} | {entry['name']}({args_full})")
                result = entry["result"] or "(no result)"
                lines.append(f"  {result}")
            else:
                lines.append(self._format_event_header(entry))
                lines.append(f"  {_format_event_message(entry)}")
            lines.append("---")
        return "\n".join(lines) if lines else "(empty trace)"

    @staticmethod
    def _format_event_header(entry: dict) -> str:
        kind_by_type = {
            "block": "BLOCKED",
            "warning": "WARNING",
            "sidechain": "SIDECHAIN",
            "append": "APPEND",
            "trace": "TRACE",
        }
        kind = kind_by_type.get(entry.get("type"), entry.get("type", "EVENT").upper())
        source = entry.get("source", "")
        suffix = " trace-only" if entry.get("trace_only") else ""
        if entry.get("name"):
            args_full = json.dumps(entry["args"], ensure_ascii=False) if entry["args"] else "{}"
            return f"Round {entry['round']} | [{kind} by {source}{suffix}] {entry['name']}({args_full})"
        call_index = entry.get("call_index")
        label = f"call#{call_index}" if call_index is not None else "agent event"
        return f"Round {entry['round']} | [{kind} by {source}{suffix}] {label}"


def _args_brief(args: dict) -> str:
    """参数的简洁表示。"""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:40] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)


def _normalize_block_message(msg: str) -> str:
    return " ".join((msg or "").split())


def _format_event_message(entry: dict) -> str:
    msg = entry.get("msg", "")
    if entry.get("type") != "block":
        return msg or "(empty event)"
    return _normalize_block_message(msg)


EFFICIENCY_FIELDS = (
    "llm_rounds",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "total_tokens",
    "embedding_calls",
    "embedding_documents",
    "embedding_tokens",
    "preprocess_llm_input_tokens",
    "preprocess_llm_cached_input_tokens",
    "preprocess_llm_uncached_input_tokens",
    "preprocess_llm_output_tokens",
    "preprocess_llm_total_tokens",
    "preprocess_embedding_tokens",
)


def empty_efficiency_metrics() -> dict:
    metrics = {field: 0 for field in EFFICIENCY_FIELDS}
    metrics["cache_accounting_source"] = "unknown"
    return metrics


def get_agent_efficiency_metrics(agent) -> dict:
    if hasattr(agent, "llm_metrics"):
        metrics = agent.llm_metrics()
        out = {field: int(metrics.get(field, 0) or 0) for field in EFFICIENCY_FIELDS}
        out["cache_accounting_source"] = str(metrics.get("cache_accounting_source") or "unknown")
        return out
    return empty_efficiency_metrics()


def aggregate_efficiency(rows: list[dict]) -> dict:
    count = len(rows)
    totals = {
        field: sum(int(row.get(field, 0) or 0) for row in rows)
        for field in EFFICIENCY_FIELDS
    }
    averages = {
        f"{field}_per_query": round(totals[field] / count, 3) if count else 0.0
        for field in EFFICIENCY_FIELDS
    }
    return {"totals": totals, "averages": averages}


def load_preprocess_metrics(summary_path: Path | None, total_queries: int) -> dict:
    if summary_path is None:
        return {}
    if not summary_path.exists():
        print(f"Warning: preprocess summary not found: {summary_path}")
        return {}
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    tokens = data.get("preprocess_tokens", {}) if isinstance(data.get("preprocess_tokens"), dict) else {}
    llm_total = int(tokens.get("llm_total_tokens", 0) or 0)
    embedding_total = int(tokens.get("embedding_total_tokens", 0) or 0)
    per_db = data.get("per_database", []) if isinstance(data.get("per_database"), list) else []
    llm_input = int(tokens.get("llm_input_tokens", 0) or 0)
    llm_cached_input = int(tokens.get("llm_cached_input_tokens", 0) or 0)
    llm_uncached_input = int(tokens.get("llm_uncached_input_tokens", 0) or 0)
    llm_output = int(tokens.get("llm_output_tokens", 0) or 0)
    if not llm_input:
        llm_input = sum(int(row.get("preprocess_llm_input_tokens", 0) or 0) for row in per_db)
    if not llm_cached_input:
        llm_cached_input = sum(int(row.get("preprocess_llm_cached_input_tokens", 0) or 0) for row in per_db)
    if not llm_uncached_input:
        llm_uncached_input = sum(int(row.get("preprocess_llm_uncached_input_tokens", 0) or 0) for row in per_db)
    if not llm_output:
        llm_output = sum(int(row.get("preprocess_llm_output_tokens", 0) or 0) for row in per_db)
    embedding_calls = sum(int(row.get("preprocess_embedding_calls", 0) or 0) for row in per_db)
    embedding_documents = sum(int(row.get("preprocess_embedding_documents", 0) or 0) for row in per_db)
    metrics = {
        "preprocess_llm_input_tokens": llm_input,
        "preprocess_llm_cached_input_tokens": llm_cached_input,
        "preprocess_llm_uncached_input_tokens": llm_uncached_input,
        "preprocess_llm_output_tokens": llm_output,
        "preprocess_llm_total_tokens": llm_total,
        "preprocess_embedding_tokens": embedding_total,
        "embedding_calls": embedding_calls,
        "embedding_documents": embedding_documents,
        "embedding_tokens": 0,
    }
    return metrics


def attach_preprocess_metrics(rows: list[dict], metrics: dict) -> None:
    if not rows or not metrics:
        return
    count = len(rows)
    for key, value in metrics.items():
        if key not in EFFICIENCY_FIELDS:
            continue
        total = int(value or 0)
        if total == 0:
            continue
        base, remainder = divmod(total, count)
        for index, row in enumerate(rows):
            row[key] = int(row.get(key, 0) or 0) + base + (1 if index < remainder else 0)


def format_efficiency_line(rows: list[dict], indent: str = "") -> str:
    eff = aggregate_efficiency(rows)
    avg = eff["averages"]
    totals = eff["totals"]
    return (
        f"{indent}Efficiency: "
        f"LLM rounds/q={avg['llm_rounds_per_query']:.2f}, "
        f"cached input tokens/q={avg['cached_input_tokens_per_query']:.1f}, "
        f"uncached input tokens/q={avg['uncached_input_tokens_per_query']:.1f}, "
        f"output tokens/q={avg['output_tokens_per_query']:.1f}, "
        f"total tokens/q={avg['total_tokens_per_query']:.1f}, "
        f"total tokens={totals['total_tokens']}"
    )


# ═══════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════

def find_db_file(db_dir: Path) -> str | None:
    for ext in DB_EXTS:
        matches = list(db_dir.glob(f"*{ext}"))
        if matches:
            return str(matches[0])
    return None


def build_agent_projects(db_id: str, use_bird_global: bool) -> list[str]:
    projects = [db_id]
    if use_bird_global:
        projects.append("bird")
    return projects


def build_bird_benchmark_system_prompt(spec, include_bird_readme: bool = True) -> list[str]:
    """Build benchmark system prompt for BIRD benchmark runs."""
    from agent.prompt import build_prompt_messages

    messages = list(build_prompt_messages(spec))
    if include_bird_readme:
        bird_readme = build_bird_readme_system_prompt()
        insert_at = len(messages)
        for idx, message in enumerate(messages):
            if message.startswith("## 当前项目") or message.startswith("## 项目 README"):
                insert_at = idx
                break
        messages.insert(insert_at, bird_readme)
    return messages


def load_query_prompt_template(args) -> str:
    prompt_file = getattr(args, "prompt_file", None)
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")

    if getattr(args, "prompt_profile", "full") == "minimal":
        return QUERY_PROMPT_MINIMAL_TEMPLATE

    return QUERY_PROMPT_BASE_TEMPLATE


def build_bird_benchmark_guardrails(args) -> list[str]:
    guardrails = list(BIRD_BENCHMARK_GUARDRAILS)
    if not getattr(args, "bird_readme", True):
        guardrails = [g for g in guardrails if g != "bird_readme_final_recheck"]
    return guardrails


def build_query_prompt(q: dict, args) -> str:
    question = q["question"]
    question_id = str(q.get("question_id", ""))
    evidence = q.get("evidence", "") or "(无额外提示)"
    current_project = q.get("db_id") or "current_project"
    bird_global_note = (
        "本次运行启用 `bird` 全局经验库。"
        if getattr(args, "use_bird_global", False)
        else "本次运行未启用 `bird` 全局经验库。"
    )
    project_scope = (
        BIRD_PROJECT_SCOPE
        if getattr(args, "use_bird_global", False)
        else LOCAL_ONLY_PROJECT_SCOPE
    ).format(current_project=current_project)
    bird_global_section = (
        BIRD_GLOBAL_PROMPT_SECTION
        if getattr(args, "use_bird_global", False)
        else ""
    )
    template = load_query_prompt_template(args)
    if getattr(args, "prompt_file", None):
        return (
            template
            .replace("{question}", question)
            .replace("{question_id}", question_id)
            .replace("{evidence}", evidence)
            .replace("{bird_global_note}", bird_global_note)
            .replace("{project_scope}", project_scope)
            .replace("{bird_global_section}", bird_global_section)
            .replace("{current_project}", current_project)
        )
    return template.format(
        question=question,
        question_id=question_id,
        evidence=evidence,
        current_project=current_project,
        bird_global_note=bird_global_note,
        project_scope=project_scope,
        bird_global_section=bird_global_section,
    )


def build_reflection_case_prompt(db_id: str, q: dict, collector: TraceCollector,
                                 predicted_sql: str | None, result_str: str,
                                 elapsed: float, use_bird_global: bool,
                                 include_bird_readme: bool,
                                 predicted_execution: set | str,
                                 golden_execution: set | str) -> str:
    template = (
        REFLECTION_CASE_PROMPT_TEMPLATE
        if use_bird_global
        else REFLECTION_CASE_NO_BIRD_PROMPT_TEMPLATE
    )
    return template.format(
        db_id=db_id,
        question_id=q.get("question_id", 0),
        difficulty=q.get("difficulty", "?"),
        result=result_str,
        elapsed=elapsed,
        question=q["question"],
        evidence=q.get("evidence", "") or "(无额外提示)",
        predicted_sql=predicted_sql or "PARSE_ERROR",
        golden_sql=q["SQL"],
        calls_summary=collector.summarize_calls(),
        blocks_summary=collector.summarize_blocks(),
        predicted_execution=format_execution_result(predicted_execution),
        golden_execution=format_execution_result(golden_execution),
        trace_detail=collector.detailed_trace_text(),
        readme_style_review_section=(
            REFLECTION_README_STYLE_REVIEW_SECTION
            if include_bird_readme
            else REFLECTION_NO_README_STYLE_REVIEW_SECTION
        ),
    )


def parse_reflection_fields(response: str) -> dict:
    """Extract structured reflection fields from the model's line-based response."""
    wanted = {
        "primary_error_category",
        "database_only_oracle_verdict",
        "decisive_db_evidence",
        "plausible_alternatives",
        "missing_prior",
        "mistake_summary",
        "minimum_fix",
        "classification_reason",
        "golden_sql_style_readme_subcategory",
        "readme_coverage_reason",
        "readme_minimum_update",
        "style_reviewer_intervention",
    }
    parsed = {}
    for line in (response or "").splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*(.*)\s*$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key in wanted:
            parsed[f"reflection_{key}"] = value
    return parsed


def run_reflection_for_case(db_id: str, q: dict,
                            agent,
                            predicted_sql: str | None, result_str: str,
                            elapsed: float, bench_dir: Path,
                            use_bird_global: bool,
                            include_bird_readme: bool,
                            predicted_execution: set | str,
                            golden_execution: set | str) -> dict:
    from agent.config import AgentSpec, DEFAULT_READONLY_TOOLS, DEFAULT_READONLY_PROMPTS
    from agent.guardrail import build_guardrails
    from agent.tools import build_registry
    from storage.workspace import Workspace

    reflection_spec = AgentSpec(
        effort="max",
        tools=list(DEFAULT_READONLY_TOOLS),
        prompts=list(DEFAULT_READONLY_PROMPTS) + ["effort"],
    )
    reflection_spec.projects = build_agent_projects(db_id, use_bird_global)
    reflection_spec.guardrails = build_guardrails(reflection_spec, ["round_limit"])

    # 方案 1：沿用同一个 agent 会话，只在反思阶段切到 reflection 配置。
    agent.tools = build_registry(reflection_spec)
    agent.workspace = Workspace(
        project_path=agent.project_path,
        active_projects=reflection_spec.projects,
    )
    agent.set_system_prompt(
        build_bird_benchmark_system_prompt(
            reflection_spec,
            include_bird_readme=include_bird_readme,
        )
    )
    agent.guardrails = reflection_spec.guardrails
    while agent.messages and agent.messages[0].get("role") == "system":
        agent.messages.pop(0)
    agent.messages = list(agent._system_messages) + agent.messages

    prompt = build_reflection_case_prompt(
        db_id=db_id,
        q=q,
        collector=agent._reflection_collector,
        predicted_sql=predicted_sql,
        result_str=result_str,
        elapsed=elapsed,
        use_bird_global=use_bird_global,
        include_bird_readme=include_bird_readme,
        predicted_execution=predicted_execution,
        golden_execution=golden_execution,
    )
    response = agent.chat(prompt)
    qid = q.get("question_id", 0)
    out = [
        f"Q{qid} [{q.get('difficulty', '?')}] {result_str} {elapsed:.1f}s",
        f"Question: {q['question']}",
        f"Evidence: {q.get('evidence', '') or '(无)'}",
        f"Predicted SQL: {predicted_sql or 'PARSE_ERROR'}",
        f"Golden SQL: {q['SQL']}",
        "Predicted execution result:",
        format_execution_result(predicted_execution),
        "Golden execution result:",
        format_execution_result(golden_execution),
        "",
        response or "",
        "",
    ]
    (bench_dir / f"q{qid}.reflection.log").write_text("\n".join(out), encoding="utf-8")
    return parse_reflection_fields(response)


def force_sql_response(agent, response: str) -> str:
    """When the benchmark agent ends without SQL, force a no-tool final answer."""
    from agent.tools import ToolRegistry

    saved_tools = agent.tools
    agent.tools = ToolRegistry()
    try:
        fallback = agent.chat(SQL_FALLBACK_PROMPT)
    finally:
        agent.tools = saved_tools

    if not fallback:
        return response or ""
    if not response:
        return fallback
    return response.rstrip() + "\n\n" + fallback


def repair_exec_error_response(agent, response: str, sql: str, error: str) -> str:
    """Ask for one no-tool SQL repair when the final SQL does not execute."""
    from agent.tools import ToolRegistry

    prompt = SQL_REPAIR_PROMPT_TEMPLATE.format(sql=sql, error=error)
    saved_tools = agent.tools
    agent.tools = ToolRegistry()
    try:
        repaired = agent.chat(prompt)
    finally:
        agent.tools = saved_tools

    if not repaired:
        return response or ""
    if not response:
        return repaired
    return response.rstrip() + "\n\n" + repaired


# ═══════════════════════════════════════════════════════════
#  汇总日志
# ═══════════════════════════════════════════════════════════

def write_db_summary(bench_dir: Path, db_id: str, results: list[dict]):
    correct = sum(1 for r in results if r['correct'])
    total = len(results)
    pct = correct / total * 100 if total else 0

    by_diff = defaultdict(lambda: [0, 0])
    for r in results:
        by_diff[r.get('difficulty', '?')][1] += 1
        if r['correct']:
            by_diff[r.get('difficulty', '?')][0] += 1

    lines = [
        f"=== {db_id} Summary ===",
        f"Total: {correct}/{total} ({pct:.1f}%)",
        format_efficiency_line(results),
        "",
        "By difficulty:",
    ]
    for diff in ["simple", "moderate", "challenging"]:
        c, t = by_diff.get(diff, [0, 0])
        if t > 0:
            lines.append(f"  {diff}: {c}/{t} ({c/t*100:.1f}%)")
    lines += ["", "Per query:"]
    for r in sorted(results, key=lambda r: r['question_id']):
        status = "OK" if r['correct'] else r['result']
        lines.append(
            f"  Q{r['question_id']} [{r.get('difficulty', '?')}] {status} {r['elapsed']:.1f}s "
            f"rounds={r.get('llm_rounds', 0)} "
            f"cached_in={r.get('cached_input_tokens', 0)} "
            f"uncached_in={r.get('uncached_input_tokens', 0)} "
            f"out={r.get('output_tokens', 0)} "
            f"total={r.get('total_tokens', 0)}"
        )
    (bench_dir / "summary.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_total_summary(output_dir: Path, all_results: list[dict]):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "benchmark_summary.log"
    by_db = defaultdict(list)
    for r in all_results:
        by_db[r['db_id']].append(r)

    lines = ["=== BIRD Benchmark Summary ===", ""]
    total_correct = total_count = 0
    for db_id in sorted(by_db.keys()):
        results = by_db[db_id]
        c = sum(1 for r in results if r['correct'])
        t = len(results)
        total_correct += c
        total_count += t
        lines.append(f"Database: {db_id} — {c}/{t} ({c/t*100:.1f}%)")
    pct = total_correct / total_count * 100 if total_count else 0
    lines.append(f"\nTotal: {total_correct}/{total_count} ({pct:.1f}%)")
    lines.append(format_efficiency_line(all_results))

    by_diff = defaultdict(list)
    for r in all_results:
        by_diff[r.get('difficulty', 'unknown')].append(r)
    lines.append("\nBy difficulty:")
    for diff in ["simple", "moderate", "challenging"]:
        results = by_diff.get(diff, [])
        if not results:
            continue
        c = sum(1 for r in results if r['correct'])
        t = len(results)
        lines.append(f"  {diff}: {c}/{t} ({c/t*100:.1f}%)")
    lines.append("\nEfficiency by database:")
    for db_id in sorted(by_db.keys()):
        lines.append(f"  {db_id}: {format_efficiency_line(by_db[db_id])}")

    text = "\n".join(lines) + "\n"
    summary_path.write_text(text, encoding="utf-8")
    print(f"\n{text}")


def write_structured_outputs(output_dir: Path, all_results: list[dict]):
    results_dir = output_dir / "results"
    evaluation_dir = output_dir / "evaluation"
    results_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)

    (results_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_results),
        encoding="utf-8",
    )
    predictions = {
        str(row["question_id"]): row.get("predicted_sql")
        for row in all_results
        if "question_id" in row
    }
    (results_dir / "predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(all_results)
    correct = sum(1 for row in all_results if row.get("correct"))
    by_db = defaultdict(list)
    by_diff = defaultdict(list)
    for row in all_results:
        by_db[row.get("db_id", "unknown")].append(row)
        by_diff[row.get("difficulty") or "unknown"].append(row)
    performance = aggregate_efficiency(all_results)
    summary = {
        "run_id": get_run_id(),
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "performance": performance,
        "efficiency": performance,
        "by_database": {
            db_id: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "accuracy": sum(1 for row in rows if row.get("correct")) / len(rows) if rows else 0.0,
                "performance": aggregate_efficiency(rows),
                "efficiency": aggregate_efficiency(rows),
            }
            for db_id, rows in sorted(by_db.items())
        },
        "by_difficulty": {
            diff: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row.get("correct")),
                "accuracy": sum(1 for row in rows if row.get("correct")) / len(rows) if rows else 0.0,
                "performance": aggregate_efficiency(rows),
                "efficiency": aggregate_efficiency(rows),
            }
            for diff, rows in sorted(by_diff.items())
        },
    }
    (evaluation_dir / "evaluation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    avg = summary["performance"]["averages"]
    totals = summary["performance"]["totals"]
    lines = [
        "# Pontis BIRD Evaluation",
        "",
        f"Total: {correct}/{total} ({summary['accuracy'] * 100:.2f}%)",
        "",
        "## Efficiency",
        "",
        f"- LLM Rounds / Query: {avg['llm_rounds_per_query']:.3f}",
        f"- Cached Input Tokens / Query: {avg['cached_input_tokens_per_query']:.3f}",
        f"- Uncached Input Tokens / Query: {avg['uncached_input_tokens_per_query']:.3f}",
        f"- Output Tokens / Query: {avg['output_tokens_per_query']:.3f}",
        f"- Total Tokens / Query: {avg['total_tokens_per_query']:.3f}",
        f"- Total Tokens: {totals['total_tokens']}",
        "",
    ]
    if totals["embedding_tokens"]:
        lines.append(f"- Embedding Tokens / Query: {avg['embedding_tokens_per_query']:.3f}")
    if totals["preprocess_llm_total_tokens"]:
        lines.append(f"- Preprocess LLM Tokens / Query: {avg['preprocess_llm_total_tokens_per_query']:.3f}")
        lines.append(f"- Preprocess LLM Cached Input Tokens / Query: {avg['preprocess_llm_cached_input_tokens_per_query']:.3f}")
        lines.append(f"- Preprocess LLM Uncached Input Tokens / Query: {avg['preprocess_llm_uncached_input_tokens_per_query']:.3f}")
        lines.append(f"- Preprocess LLM Output Tokens / Query: {avg['preprocess_llm_output_tokens_per_query']:.3f}")
    if totals["preprocess_embedding_tokens"]:
        lines.append(f"- Preprocess Embedding Tokens / Query: {avg['preprocess_embedding_tokens_per_query']:.3f}")
    if len(lines) > 0 and lines[-1]:
        lines.append("")
    lines.append("## By Database")
    for db_id, item in summary["by_database"].items():
        lines.append(f"- {db_id}: {item['correct']}/{item['total']} ({item['accuracy'] * 100:.2f}%)")
    lines.extend(["", "## By Difficulty"])
    for diff, item in summary["by_difficulty"].items():
        lines.append(f"- {diff}: {item['correct']}/{item['total']} ({item['accuracy'] * 100:.2f}%)")
    (evaluation_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ═══════════════════════════════════════════════════════════
#  进度追踪
# ═══════════════════════════════════════════════════════════

class ProgressTracker:
    """线程安全的进度记录器。"""

    def __init__(self, db_map: dict[str, list], progress_path: Path):
        self._lock = threading.Lock()
        self._path = progress_path
        self._states: dict[str, dict] = {
            db_id: {
                "total": len(qs), "status": "pending",
                "done": 0, "correct": 0,
                "started_at": None, "finished_at": None,
            }
            for db_id, qs in db_map.items()
        }
        self._write()

    def start_test(self, db_id: str):
        with self._lock:
            self._states[db_id]["status"] = "testing"
            if self._states[db_id]["started_at"] is None:
                self._states[db_id]["started_at"] = time.time()
            self._write()

    def update(self, db_id: str, done: int, correct: int):
        with self._lock:
            self._states[db_id]["done"] = done
            self._states[db_id]["correct"] = correct
            self._write()

    def finish(self, db_id: str, correct: int, total: int):
        with self._lock:
            self._states[db_id]["status"] = "done"
            self._states[db_id]["done"] = total
            self._states[db_id]["correct"] = correct
            self._states[db_id]["finished_at"] = time.time()
            self._write()

    def _write(self):
        lines = [f"=== Progress — {time.strftime('%Y-%m-%d %H:%M:%S')} ===", ""]
        total_done = sum(s["done"] for s in self._states.values())
        total_queries = sum(s["total"] for s in self._states.values())
        total_correct = sum(s["correct"] for s in self._states.values())
        lines.append(f"Overall: {total_done}/{total_queries} queries, {total_correct} correct")
        lines.append("")
        for db_id in sorted(self._states.keys()):
            s = self._states[db_id]
            pct = s["done"] / s["total"] * 100 if s["total"] else 0
            elapsed = ""
            if s["started_at"] and s["status"] != "done":
                elapsed = f" ({time.time() - s['started_at']:.0f}s)"
            elif s["started_at"] and s["finished_at"]:
                elapsed = f" ({s['finished_at'] - s['started_at']:.0f}s)"
            lines.append(
                f"  [{s['status']:>10}] {db_id:25s} "
                f"{s['done']:>4}/{s['total']:<4} ({pct:5.1f}%) "
                f"correct={s['correct']}{elapsed}"
            )
        lines.append("")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cleanup_all(db_base: Path, db_map: dict[str, list], *, train: bool):
    print("=== Cleanup ===")
    for db_id in sorted(db_map.keys()):
        db_dir = db_base / db_id
        bench_dir = get_benchmark_dir(db_id, train)

        if bench_dir.exists():
            count = 0
            for old_log in bench_dir.glob("*.log"):
                old_log.unlink(missing_ok=True)
                count += 1
            if count:
                print(f"  [{db_id}] Cleared {count} logs")

        legacy_pontis_dir = db_dir / ".pontis"
        if legacy_pontis_dir.exists():
            import shutil
            shutil.rmtree(legacy_pontis_dir, ignore_errors=True)
            print(f"  [{db_id}] Removed legacy data .pontis")
    print("Cleanup done\n")


def cleanup_bird_global(clear_bird_knowledge: bool = False):
    if not clear_bird_knowledge:
        return

    from storage.workspace import Workspace

    print("=== Cleanup bird ===")
    ws = Workspace(active_projects=["bird"])
    rows = ws.cypher("MATCH (n) WHERE n.name != 'README' RETURN n", project="bird")
    total = len(rows)
    if not total:
        print("  [bird] No non-README knowledge nodes to delete")
        print("Cleanup bird done\n")
        return

    ws.cypher("MATCH (n) WHERE n.name != 'README' DELETE n", project="bird")
    print(f"  [bird] Deleted {total} non-README nodes")
    print("Cleanup bird done\n")


def ensure_bird_global_ready(args) -> None:
    """Make --use-bird-global fail fast or populate bird before benchmark."""
    if not getattr(args, "use_bird_global", False):
        return

    from storage.workspace import Workspace
    from scripts.BIRD.bird_readme import sync_bird_readme
    from scripts.BIRD.sync_bird_global import (
        count_bird_train_examples,
        resolve_train_json_path,
        sync_bird_global,
    )

    ws = Workspace(active_projects=["bird"])
    sync_bird_readme(ws)
    count = count_bird_train_examples(ws)
    if count > 0:
        print(f"=== bird global ===\n  Synced README\n  Found {count} imported train examples\n")
        return

    train_json = resolve_train_json_path(getattr(args, "bird_train_json", None))
    if not getattr(args, "auto_sync_bird_global", True):
        print(
            "Error: --use-bird-global is enabled but bird has 0 imported train examples.\n"
            f"Run: python Pontis/scripts/BIRD/sync_bird_global.py --train-json {train_json}\n"
            "Or pass --no-bird-global."
        )
        sys.exit(1)

    if not train_json.exists():
        print(
            "Error: --use-bird-global is enabled but bird has 0 imported train examples, "
            f"and train.json was not found: {train_json}"
        )
        sys.exit(1)

    print("=== bird global ===")
    print(f"  Empty bird graph; syncing train examples from {train_json}")
    sync_bird_global(
        import_train=True,
        embed_train=not getattr(args, "no_bird_global_embedding", False),
        train_json=train_json,
    )
    count = count_bird_train_examples(ws)
    if count <= 0:
        print("Error: bird global sync completed but no train examples were imported")
        sys.exit(1)
    print(f"  Ready: {count} imported train examples\n")


# ═══════════════════════════════════════════════════════════
#  单库完整流程
# ═══════════════════════════════════════════════════════════

def run_database(db_id: str, queries: list[dict], db_base: Path,
                 args, tracker: ProgressTracker) -> list[dict]:
    db_dir = db_base / db_id
    print(f"[{db_id}] {len(queries)} queries — start")

    if not db_dir.exists():
        print(f"[{db_id}] Error: directory not found, skipping")
        return []

    # Phase 1: 找数据库文件
    db_path = find_db_file(db_dir)
    if not db_path:
        print(f"[{db_id}] Error: no database file found")
        return []

    # Phase 2: 测试
    bench_dir = get_benchmark_dir(db_id, args.train)
    bench_dir.mkdir(parents=True, exist_ok=True)

    tracker.start_test(db_id)

    def run_one(q: dict) -> dict:
        from agent.config import create_agent, AgentSpec
        from agent.guardrail import build_guardrails

        qid = q.get('question_id', 0)
        collector = TraceCollector()

        spec = AgentSpec(
            effort="max",
            tools=list(BIRD_BENCHMARK_TOOLS),
            prompts=list(BIRD_BENCHMARK_PROMPTS),
        )
        spec.projects = build_agent_projects(db_id, args.use_bird_global)
        spec.guardrails = build_guardrails(spec, build_bird_benchmark_guardrails(args))
        agent = create_agent(
            str(db_dir),
            spec,
            trace_callback=collector.callback,
        )
        agent.set_system_prompt(
            build_bird_benchmark_system_prompt(
                spec,
                include_bird_readme=args.bird_readme,
            )
        )
        agent._reflection_collector = collector

        prompt = build_query_prompt(q, args)

        t0 = time.time()
        try:
            response = agent.chat(prompt)
            if extract_sql(response) is None:
                response = force_sql_response(agent, response)
            elapsed = time.time() - t0
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  Q{qid} [{q.get('difficulty', '?')}] ERROR: {e}")
            error_response = f"ERROR: {type(e).__name__}: {e}"
            efficiency = get_agent_efficiency_metrics(agent)
            collector.write_logs(bench_dir, qid, q, error_response, None, "ERROR", elapsed, efficiency)
            return {'run_id': get_run_id(), 'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                    'question': q.get('question'), 'evidence': q.get('evidence', ''),
                    'golden_sql': q.get('SQL'), 'predicted_sql': None,
                    'correct': False, 'result': "ERROR", 'elapsed': round(elapsed, 1),
                    **efficiency,
                    'use_bird_global': args.use_bird_global,
                    'bird_readme': args.bird_readme,
                    'prompt_profile': args.prompt_profile,
                    'prompt_file': str(args.prompt_file) if args.prompt_file else None}

        predicted_sql = extract_sql(response)
        golden_result = execute_sql(db_path, q['SQL'])
        predicted_result = execute_sql(db_path, predicted_sql) if predicted_sql else "PARSE_ERROR"
        if (
            predicted_sql
            and isinstance(predicted_result, str)
            and getattr(args, "exec_repair", True)
        ):
            response = repair_exec_error_response(agent, response, predicted_sql, predicted_result)
            repaired_sql = extract_sql(response)
            if repaired_sql and repaired_sql != predicted_sql:
                predicted_sql = repaired_sql
                predicted_result = execute_sql(db_path, predicted_sql)
        elapsed = time.time() - t0
        correct = is_correct(predicted_result, golden_result)

        result_str = (
            "CORRECT" if correct
            else "PARSE_ERROR" if predicted_sql is None
            else "EXEC_ERROR" if isinstance(predicted_result, str)
            else "WRONG"
        )

        efficiency = get_agent_efficiency_metrics(agent)
        collector.write_logs(bench_dir, qid, q, response, predicted_sql, result_str, elapsed, efficiency)

        reflection_fields = {}
        if args.reflection and not correct:
            try:
                reflection_fields = run_reflection_for_case(
                    db_id=db_id,
                    agent=agent,
                    q=q,
                    predicted_sql=predicted_sql,
                    result_str=result_str,
                    elapsed=elapsed,
                    bench_dir=bench_dir,
                    use_bird_global=args.use_bird_global,
                    include_bird_readme=args.bird_readme,
                    predicted_execution=predicted_result,
                    golden_execution=golden_result,
                )
            except Exception as e:
                print(f"  Q{qid} reflection ERROR: {e}")
                reflection_fields = {"reflection_error": f"{type(e).__name__}: {e}"}

        status = "OK" if correct else "FAIL"
        print(
            f"  Q{qid} [{q.get('difficulty', '?')}] {status} {result_str} ({elapsed:.1f}s) "
            f"rounds={efficiency['llm_rounds']} tokens={efficiency['total_tokens']}"
        )

        return {'run_id': get_run_id(), 'db_id': db_id, 'question_id': qid, 'difficulty': q.get('difficulty', '?'),
                'question': q.get('question'), 'evidence': q.get('evidence', ''),
                'golden_sql': q.get('SQL'), 'predicted_sql': predicted_sql,
                'correct': correct, 'result': result_str, 'elapsed': round(elapsed, 1),
                **efficiency,
                **reflection_fields,
                'use_bird_global': args.use_bird_global,
                'bird_readme': args.bird_readme,
                'prompt_profile': args.prompt_profile,
                'prompt_file': str(args.prompt_file) if args.prompt_file else None}

    db_results = []
    correct_so_far = 0
    done_so_far = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, q): q['question_id'] for q in queries}
        for future in as_completed(futures):
            result = future.result()
            db_results.append(result)
            done_so_far += 1
            if result['correct']:
                correct_so_far += 1
            tracker.update(db_id, done_so_far, correct_so_far)

    db_results.sort(key=lambda r: r['question_id'])
    write_db_summary(bench_dir, db_id, db_results)

    correct_count = sum(1 for r in db_results if r['correct'])
    pct = correct_count / len(queries) * 100 if queries else 0
    print(f"[{db_id}] => {correct_count}/{len(queries)} ({pct:.1f}%)")

    tracker.finish(db_id, correct_count, len(queries))
    return db_results


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIRD Text-to-SQL Benchmark")
    parser.add_argument("--train", action="store_true", help="跑 train 集（默认跑 dev）")
    parser.add_argument("--db", help="只测试指定数据库；多个库用逗号分隔")
    parser.add_argument("--skip-extract", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workers", type=int, default=1, help="每库并行 worker（默认 1）")
    parser.add_argument("--db-workers", type=int, default=1, help="并行数据库数（默认 1）")
    parser.add_argument("--qids", help="只测试指定 question_id，逗号分隔")
    parser.add_argument("--limit", type=int, help="每库最多测试 N 条")
    parser.add_argument("--run-id", help="输出目录 run id；默认使用当前时间戳 YYYYmmdd_HHMMSS")
    parser.add_argument("--reflection", action="store_true", help="错题验证后立即运行 reflection，不再读日志二次分析")
    parser.add_argument(
        "--no-exec-repair",
        dest="exec_repair",
        action="store_false",
        default=True,
        help="最终 SQL 执行失败时不追加一次无工具修复",
    )
    parser.add_argument(
        "--use-bird-global",
        dest="use_bird_global",
        action="store_true",
        default=False,
        help="启用 bird 全局经验库（默认关闭）",
    )
    parser.add_argument(
        "--no-bird-global",
        dest="use_bird_global",
        action="store_false",
        help="禁用 bird 全局经验库，只使用当前数据库项目（默认）",
    )
    parser.add_argument(
        "--prompt-profile",
        choices=PROMPT_PROFILES,
        default="full",
        help="主求解 prompt 档位：full 保留完整规则，minimal 只保留最小输出协议",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help=(
            "使用自定义主求解 prompt 模板；可包含 {question}、{evidence}、"
            "{bird_global_note}、{project_scope}、{bird_global_section}"
        ),
    )
    parser.add_argument(
        "--bird-readme",
        dest="bird_readme",
        action="store_true",
        default=True,
        help="把 Pontis/scripts/BIRD/bird_readme.py 注入系统提示词（默认开启）",
    )
    parser.add_argument(
        "--no-bird-readme",
        dest="bird_readme",
        action="store_false",
        help="关闭 BIRD README 系统提示词注入和最终复审 guardrail，用于 ablation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for structured results and evaluation summaries.",
    )
    parser.add_argument(
        "--preprocess-summary",
        type=Path,
        default=None,
        help="Pontis extract_summary.json to merge preprocessing token metrics into evaluation outputs.",
    )
    parser.add_argument(
        "--clear-bird-knowledge",
        action="store_true",
        help="运行前清空 bird 全局知识库中除 README 外的所有节点",
    )
    parser.add_argument(
        "--no-auto-sync-bird-global",
        dest="auto_sync_bird_global",
        action="store_false",
        default=True,
        help="启用 bird 全局库但库为空时直接失败，不自动导入 train examples",
    )
    parser.add_argument(
        "--no-bird-global-embedding",
        action="store_true",
        help="自动同步 bird 全局库时只导入 train examples，不生成语义向量",
    )
    parser.add_argument(
        "--bird-train-json",
        type=Path,
        help="用于同步 bird 全局经验库的 BIRD train.json 路径",
    )
    args = parser.parse_args()

    if args.run_id:
        set_run_id(args.run_id)

    if args.prompt_file and not args.prompt_file.exists():
        print(f"Error: prompt file not found: {args.prompt_file}")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # 根据数据集选路径
    data_dir = get_data_dir(args.train)
    json_path = data_dir / ("train.json" if args.train else "dev.json")
    db_base = get_db_base(args.train)

    if not json_path.exists():
        print(f"Error: {json_path} not found")
        sys.exit(1)

    if args.preprocess_summary is None:
        candidate = PONTIS_WORKSPACE_ROOT / "preprocess_logs" / get_run_name(args.train) / "extract_summary.json"
        if candidate.exists():
            args.preprocess_summary = candidate

    data = assign_question_ids(json.loads(json_path.read_text(encoding="utf-8")))
    if args.db:
        db_filter = {x.strip() for x in args.db.split(",") if x.strip()}
        data = [q for q in data if q['db_id'] in db_filter]
    if args.qids:
        qid_set = {int(x.strip()) for x in args.qids.split(",")}
        data = [q for q in data if q["question_id"] in qid_set]

    by_db = defaultdict(list)
    for q in data:
        by_db[q['db_id']].append(q)

    if args.limit:
        for db_id in by_db:
            by_db[db_id] = by_db[db_id][:args.limit]

    total_queries = sum(len(qs) for qs in by_db.values())
    mode_label = "Train" if args.train else "Dev"
    print(f"=== BIRD {mode_label} Benchmark ===")
    print(f"Databases: {len(by_db)}, Queries: {total_queries}")
    print(f"DB workers: {args.db_workers}, Query workers/db: {args.workers}\n")
    print(f"Run id: {get_run_id()}")
    print(f"Preprocess logs: {PONTIS_WORKSPACE_ROOT / 'preprocess_logs' / get_run_name(args.train)}")
    print(f"Runtime logs: {PONTIS_WORKSPACE_ROOT / 'runtime_logs' / get_run_name(args.train)}")
    print(
        "Config: "
        f"bird_global={'on' if args.use_bird_global else 'off'}, "
        f"bird_readme={'on' if args.bird_readme else 'off'}, "
        f"prompt_profile={args.prompt_profile}, "
        f"prompt_file={args.prompt_file or '(none)'}\n"
    )

    if args.output_dir is None:
        args.output_dir = get_results_dir(args.train)
    print(f"Results: {args.output_dir}\n")
    if args.preprocess_summary:
        print(f"Preprocess summary: {args.preprocess_summary}\n")

    cleanup_all(db_base, by_db, train=args.train)
    if args.clear_bird_knowledge and not args.use_bird_global:
        print("Skip --clear-bird-knowledge because --no-bird-global is set\n")
    else:
        cleanup_bird_global(clear_bird_knowledge=args.clear_bird_knowledge)
    ensure_bird_global_ready(args)

    progress_path = get_progress_path(args.train)
    tracker = ProgressTracker(by_db, progress_path)

    all_results = []
    with ThreadPoolExecutor(max_workers=args.db_workers) as db_pool:
        futures = {
            db_pool.submit(run_database, db_id, queries, db_base, args, tracker): db_id
            for db_id, queries in sorted(by_db.items())
        }
        for future in as_completed(futures):
            db_id = futures[future]
            try:
                db_results = future.result()
                all_results.extend(db_results)
            except Exception as e:
                print(f"[{db_id}] FATAL: {e}")

    if all_results:
        all_results.sort(key=lambda r: (r['db_id'], r['question_id']))
        attach_preprocess_metrics(
            all_results,
            load_preprocess_metrics(args.preprocess_summary, len(all_results)),
        )
        write_total_summary(args.output_dir / "evaluation", all_results)
        write_structured_outputs(args.output_dir, all_results)


if __name__ == "__main__":
    main()
