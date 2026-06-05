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
import sys
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
from scripts.BIRD.benchmark_runtime import (
    ProgressTracker,
    TraceCollector,
    aggregate_efficiency,
    attach_preprocess_metrics,
    execute_sql,
    extract_sql,
    find_db_file,
    format_efficiency_line,
    format_execution_result,
    get_agent_efficiency_metrics,
    is_correct,
    load_preprocess_metrics,
)

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
    "bird_readme_final_recheck", "final_sql_validity_check",
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

请根据以下信息生成一条 SQLite SQL 查询。

Question ID: {question_id}

问题：{question}

提示：{evidence}

"""

LOCAL_ONLY_PROJECT_SCOPE = """\
本次运行只打开当前数据库项目：`{current_project}`。
"""


QUERY_PROMPT_MINIMAL_TEMPLATE = """\
请根据以下 BIRD 问题生成一条 SQLite SQL 查询。
{project_scope}

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
- 主解题阶段可能启用了 `BirdReadmeFinalRecheck`。它会先从 BIRD README 中用语义、关键词和 SQL 特征检索候选规则，再让独立 reviewer 判断当前 question/evidence/predicted SQL 是否违反这些候选规则；guardrail block 只把 reviewer 选中的规则回灌给主 agent。
- 以 `Guardrail / blocks` 和详细执行轨迹为准判断 recheck 是否真正介入；出现 `BirdReadmeFinalRecheck` 的 block 就说明 reviewer 已介入，但不代表完整 README 已回灌给主 agent。
- 为兼容旧评测 schema，若 `primary_error_category` 是 `GOLDEN_SQL_STYLE`，必须额外输出 `style_reviewer_intervention`：
  - `REVIEWER_INTERVENED_BUT_NOT_FOLLOWED`：final README recheck 已经 BLOCK，精选规则若被执行通常会更接近 golden，但主 agent 最终没有执行或只部分执行。
  - `REVIEWER_INTERVENED_WITH_WRONG_ADVICE`：reviewer 精选规则本身错误、遗漏关键规则，或明显误导了主 agent。
  - `REVIEWER_NOT_INTERVENED`：没有 `BirdReadmeFinalRecheck` block，或精选规则没有覆盖关键 style 问题。
- 若 `primary_error_category` 不是 `GOLDEN_SQL_STYLE`，`style_reviewer_intervention` 写 `not_applicable`。
"""

REFLECTION_NO_README_STYLE_REVIEW_SECTION = """\

README 覆盖性复盘要求：
- 本次运行未启用 BIRD README。
- `golden_sql_style_readme_subcategory` 固定写 `not_applicable`。
- `readme_coverage_reason` 和 `readme_minimum_update` 固定写 none。

Final README recheck 介入复盘要求：
- 主解题阶段可能启用了 `BirdReadmeFinalRecheck`。若 BIRD README 被关闭，该 guardrail 通常也会被关闭；若仍出现 block，说明 reviewer 只把精选规则或 fallback 规则回灌给主 agent，不回灌完整 README。
- 以 `Guardrail / blocks` 和详细执行轨迹为准判断它是否真正介入；没有 `BirdReadmeFinalRecheck` block，或精选规则没有覆盖关键 style 问题，归为 `REVIEWER_NOT_INTERVENED`。
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


def assign_question_ids(questions: list[dict]) -> list[dict]:
    """为没有 question_id 的数据集补一个稳定 id。"""
    normalized = []
    for idx, q in enumerate(questions):
        item = dict(q)
        if item.get("question_id") is None:
            item["question_id"] = idx
        normalized.append(item)
    return normalized


def build_agent_projects(db_id: str) -> list[str]:
    return [db_id]


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
    if getattr(args, "bird_schema_challenge", False) or getattr(args, "bird_multi_report", False):
        insert_at = guardrails.index("bird_readme_final_recheck") if "bird_readme_final_recheck" in guardrails else len(guardrails)
        guardrails.insert(insert_at, "bird_schema_challenge_controller")
    if not getattr(args, "bird_readme", True):
        guardrails = [g for g in guardrails if g != "bird_readme_final_recheck"]
    return guardrails


def include_bird_readme_prompt(args) -> bool:
    value = getattr(args, "bird_readme_prompt", None)
    if value is None:
        return bool(getattr(args, "bird_readme", True))
    return bool(value)


def build_query_prompt(q: dict, args) -> str:
    question = q["question"]
    question_id = str(q.get("question_id", ""))
    evidence = q.get("evidence", "") or "(无额外提示)"
    current_project = q.get("db_id") or "current_project"
    project_scope = LOCAL_ONLY_PROJECT_SCOPE.format(current_project=current_project)
    template = load_query_prompt_template(args)
    if getattr(args, "prompt_file", None):
        return (
            template
            .replace("{question}", question)
            .replace("{question_id}", question_id)
            .replace("{evidence}", evidence)
            .replace("{project_scope}", project_scope)
            .replace("{current_project}", current_project)
        )
    return template.format(
        question=question,
        question_id=question_id,
        evidence=evidence,
        current_project=current_project,
        project_scope=project_scope,
    )


def build_reflection_case_prompt(db_id: str, q: dict, collector: TraceCollector,
                                 predicted_sql: str | None, result_str: str,
                                 elapsed: float, include_bird_readme: bool,
                                 predicted_execution: set | str,
                                 golden_execution: set | str) -> str:
    return REFLECTION_CASE_PROMPT_TEMPLATE.format(
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
    """Extract structured reflection fields from common text/JSON variants."""
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
    enum_values = {
        "primary_error_category": (
            "DB_EXPLORATION_FIXABLE",
            "DATASET_PRIOR_REQUIRED",
            "GOLDEN_SQL_STYLE",
        ),
        "golden_sql_style_readme_subcategory": (
            "README_RULE_CLEAR_BUT_NOT_FOLLOWED",
            "README_RULE_WRONG_OR_UNCLEAR",
            "README_STYLE_NOT_COVERED",
            "not_applicable",
        ),
        "style_reviewer_intervention": (
            "REVIEWER_NOT_INTERVENED",
            "REVIEWER_INTERVENED_WITH_CORRECT_ADVICE",
            "REVIEWER_INTERVENED_WITH_WRONG_ADVICE",
            "REVIEWER_INTERVENED_BUT_NOT_FOLLOWED",
            "not_applicable",
        ),
    }

    def add_field(key: str, value) -> None:
        key = str(key).strip().strip("`*_").strip()
        if key not in wanted or value is None:
            return
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        rendered = re.sub(r"^[\s`*_]+|[\s`*_]+$", "", rendered).strip()
        for allowed in enum_values.get(key, ()):
            if re.search(rf"\b{re.escape(allowed)}\b", rendered):
                rendered = allowed
                break
        parsed[f"reflection_{key}"] = rendered

    text = response or ""

    # Some reflection calls return a fenced or bare JSON object instead of
    # strict line-based key/value output.
    json_candidates = [
        m.group(1)
        for m in re.finditer(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        json_candidates.append(stripped)
    for candidate in json_candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            for key, value in obj.items():
                add_field(str(key), value)

    # Tolerate Markdown variants such as "**key**: value", "`key: value`",
    # bullet-prefixed fields, and Chinese colons.
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("```"):
            continue
        if cleaned.startswith("`") and cleaned.endswith("`"):
            cleaned = cleaned[1:-1].strip()
        match = re.match(
            r"^\s*(?:[-*]\s*)?(?:\*\*|__)?`?([A-Za-z0-9_]+)`?"
            r"(?:\*\*|__)?\s*[:：]\s*(.*?)\s*$",
            cleaned,
        )
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        add_field(key, value)

    primary_key = "reflection_primary_error_category"
    if primary_key not in parsed:
        for category in (
            "DB_EXPLORATION_FIXABLE",
            "DATASET_PRIOR_REQUIRED",
            "GOLDEN_SQL_STYLE",
        ):
            if re.search(rf"\b{re.escape(category)}\b", text):
                parsed[primary_key] = category
                break

    if parsed.get(primary_key) != "GOLDEN_SQL_STYLE":
        parsed.setdefault(
            "reflection_golden_sql_style_readme_subcategory",
            "not_applicable",
        )
        parsed.setdefault(
            "reflection_style_reviewer_intervention",
            "not_applicable",
        )
    return parsed


def run_reflection_for_case(db_id: str, q: dict,
                            agent,
                            predicted_sql: str | None, result_str: str,
                            elapsed: float, bench_dir: Path,
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
    reflection_spec.projects = build_agent_projects(db_id)
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
        spec.bird_report_count = max(1, int(getattr(args, "bird_report_count", 3) or 3))
        spec.projects = build_agent_projects(db_id)
        spec.guardrails = build_guardrails(spec, build_bird_benchmark_guardrails(args))
        agent = create_agent(
            str(db_dir),
            spec,
            trace_callback=collector.callback,
        )
        agent.set_system_prompt(
            build_bird_benchmark_system_prompt(
                spec,
                include_bird_readme=include_bird_readme_prompt(args),
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
                    'bird_readme': args.bird_readme,
                    'bird_readme_prompt': include_bird_readme_prompt(args),
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
                'bird_readme': args.bird_readme,
                'bird_readme_prompt': include_bird_readme_prompt(args),
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
    parser.add_argument("--no-bird-global", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--prompt-profile",
        choices=PROMPT_PROFILES,
        default="full",
        help="主求解 prompt 档位：full 保留完整规则，minimal 只保留最小输出协议",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="使用自定义主求解 prompt 模板；可包含 {question}、{evidence}、{project_scope}",
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
        help="关闭 BIRD README 最终复审 guardrail；若未单独设置 prompt 开关，也同时关闭主提示词 README 注入",
    )
    parser.add_argument(
        "--bird-readme-prompt",
        dest="bird_readme_prompt",
        action="store_true",
        default=None,
        help="把 BIRD README 全量注入主求解系统提示词；默认跟随 --bird-readme",
    )
    parser.add_argument(
        "--no-bird-readme-prompt",
        dest="bird_readme_prompt",
        action="store_false",
        help="不把 BIRD README 全量注入主求解系统提示词，但不关闭最终 README reviewer",
    )
    parser.add_argument(
        "--bird-schema-challenge",
        action="store_true",
        default=False,
        help="启用最终 SQL schema-linking challenge + judge 上下文重写控制器（默认关闭）",
    )
    parser.add_argument(
        "--bird-multi-report",
        dest="bird_multi_report",
        action="store_true",
        default=False,
        help="兼容旧参数；等价于 --bird-schema-challenge",
    )
    parser.add_argument(
        "--bird-report-count",
        type=int,
        default=3,
        help="启用 --bird-schema-challenge 时生成的 SQL report 总数，默认 3（主 agent 1 份 + challenger 2 份）",
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
        f"bird_readme={'on' if args.bird_readme else 'off'}, "
        f"bird_readme_prompt={'on' if include_bird_readme_prompt(args) else 'off'}, "
        f"bird_schema_challenge={'on' if (args.bird_schema_challenge or args.bird_multi_report) else 'off'}, "
        f"bird_report_count={args.bird_report_count}, "
        f"prompt_profile={args.prompt_profile}, "
        f"prompt_file={args.prompt_file or '(none)'}\n"
    )

    if args.output_dir is None:
        args.output_dir = get_results_dir(args.train)
    print(f"Results: {args.output_dir}\n")
    if args.preprocess_summary:
        print(f"Preprocess summary: {args.preprocess_summary}\n")

    cleanup_all(db_base, by_db, train=args.train)

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
