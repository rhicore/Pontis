"""BIRD-specific entity hints — 只写数据库事实，不写分析口径处方。

该 explorer 为 BIRD 数据库维护 `disambig`、`hint` 实体和基础实体
`hints` 属性。目标是让 benchmark 主 agent 在 `meta` 时直接看到：

- 行粒度事实
- 字段落点边界
- JOIN 键和格式事实
- 枚举/代码/显示字段/标识字段的语义
- 快照、多版本、多日期记录等结构事实

不在这里写数据集输出风格、SQL 处方或业务清洗规则；这些由 BIRD README
和 reviewer 处理。
"""

from __future__ import annotations

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)


PROMPT = """\
你的任务是为当前 BIRD 数据库维护基础实体 `hints`，并在确有必要时补充少量 `disambig` / `hint` 实体。

## 任务目标

把当前数据库中稳定、可验证、可复用的结构事实写进图谱，让下游使用者在 `meta`
表或列时，能直接看到：

- 表的一行代表什么
- 自然语言概念落到哪个字段
- JOIN 使用哪个键，是否有格式风险
- 是否存在多日期快照、多版本、多角色
- 展示字段与标识字段如何分工
- 枚举值、哨兵值、代码值的已知含义

这里只维护数据库事实层，不写 SQL 处方，不写 benchmark 风格口径。

## 写作边界

- 写入内容只陈述数据库事实，不写操作指导。
- `hints` 字段只存放短事实；内容采用事实边界、格式、粒度、覆盖范围和值域差异表述。
- 消歧内容用于辨析差异；两个实体即使名称或业务主题接近，也按不同实体描述。
- 描述实体之间的可比性、覆盖范围、值域交集和结构关系；把看似接近的实体写成差异比较。

## 工作方式

按下面三步完成：

1. 先找出当前数据库里最关键的 3-6 张表。
2. 再为这些表挑出最关键的字段：显示名、标识字段、JOIN 键、日期/版本字段、状态/类型字段、核心度量字段。
3. 一旦确认某个事实，就立即写回对应实体；不要把所有写操作放到最后。

使用现有 `meta` 中的 `brief/detail`、列说明、外键关系和项目内说明文件。当前任务默认不做数据库值普查；已有元数据和说明文本已经足以支持大部分事实写回。

如项目中存在 `database_description`、字段说明或官方数据字典文件，先 `find` / `meta`
确认其用途，再用 `read` 读取与当前高价值表列直接相关的原文。

## 写回位置

### 基础实体 `hints`

基础实体 `hints` 是首选落点。每条 hint 用一句中文短事实，覆盖这些内容：

- 核心事实表、维表、快照表的表级 row_grain
- 关键显示字段、关键标识字段、关键 JOIN 键
- 关键日期/版本字段、状态/类型字段
- 明显的格式风险或枚举边界

表级和列级事实通过 `update_meta(..., {"hints": [...]})` 写回对应表或列。

表达方式参考：

- `一行代表一条学校级或学区级 SAT 汇总记录。`
- `同一 player_api_id 存在按 date 记录的多条属性快照。`
- `通过 CDSCode 关联 schools；satscores.cds 可能需要补前导零。`
- `FundingType 记录特许学校资助类型；Charter 记录是否为特许学校。`
- `DisplayName 是展示名；Id 是本地用户标识。`

### `disambig` 实体

只有在多个候选实体会被同一自然语言表达真实混用时，才创建 `disambig`。

`disambig` 需要写清：

- 歧义词是什么
- 每个候选实体各表示什么
- 它们的边界在哪里

### `hint` 实体

只有在知识必须跨多个实体比较时，才创建 `hint`。

适合创建 `hint` 的情况：

- 两张表分别承担度量和维度属性
- 两条 JOIN 路径会把结果带到不同粒度
- 两个近名字段分别承担展示字段和标识字段角色

普通单实体事实直接写回基础实体 `hints`，避免额外创建重复的 `X:hint` 节点。

## 产出范围

以高质量、小而准为目标。每个数据库通常完成这些内容就够了：

- 3-6 个表级 `hints`
- 6-15 个列级 `hints`
- 0-3 个 `disambig`
- 0-3 个跨实体 `hint`

## 质量要求

- 所有内容都写数据库事实。
- 只写能被当前 `meta` 或说明文本直接支持的事实。
- 同一事实写在最直接的位置；短事实放基础实体 `hints`。
- `disambig` 和 `hint` 只保留真正改变 schema linking 或粒度判断的内容。
- 用中文写 brief、detail 和 hints。

## 完成标准

结束前确认：

1. 核心表已经有表级 `hints`。
2. 关键字段已经有列级 `hints`。
3. 只有真实需要跨实体比较时才创建了 `hint` / `disambig`。
4. 写入已经完成后再结束任务。
"""


def generate(workspace: Workspace) -> None:
    """维护 BIRD 数据库的事实型 hints。"""
    from agent.config import AgentSpec, create_agent
    from agent.guardrail import build_guardrails
    from agent.utils import load_agent_config

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping bird entity hints")
        return

    logger.info("=== BIRD Entity Hints ===")

    spec = AgentSpec(
        effort="mid",
        max_rounds=36,
        tools=[
            "find", "meta", "read",
            "create_entity", "update_meta",
        ],
        prompts=["base", "tool", "project"],
        projects=[workspace.active_projects[0]] if workspace.active_projects else [],
    )
    spec.guardrails = build_guardrails(spec, ["round_limit"])
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== BIRD Entity Hints done ===")
    return _preprocess_metrics(agent)


def _preprocess_metrics(agent) -> dict:
    if not hasattr(agent, "llm_metrics"):
        return {}
    metrics = agent.llm_metrics()
    return {
        "preprocess_llm_calls": int(metrics.get("llm_rounds", 0) or 0),
        "preprocess_llm_input_tokens": int(metrics.get("input_tokens", 0) or 0),
        "preprocess_llm_cached_input_tokens": int(metrics.get("cached_input_tokens", 0) or 0),
        "preprocess_llm_uncached_input_tokens": int(metrics.get("uncached_input_tokens", 0) or 0),
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }
