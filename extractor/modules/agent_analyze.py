"""Agent Analyze — 统一分析协调者：关系发现 + 总结生成

一个主 agent 自主探索项目数据，完成两大目标：
  1. 发现列之间的关系并创建 .rel 实体
  2. 为所有实体生成 brief / detail 总结

子智能体用于处理具体的单表/单任务，降低单次对话的复杂度。

独立执行:
    python -m extractor.agent_analyze ./my_data
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

MAX_ROUNDS_DEFAULT = 150

COORDINATOR_PROMPT = """\
你是数据分析助手，负责深入理解用户的项目数据。

## 你的两大目标

1. **关系发现**：找出数据库列之间的有意义关联，创建 .rel 实体，为已有的显式fk写ai summary
2. **总结生成**：为所有实体（表、列、文件）写 brief 和 detail

这两个目标没有严格的先后顺序。你可能在理解某张表时发现了关系，也可能在 \
分析关系后对某张表有了新的理解。按你觉得合理的方式推进。

## 守则

- **基于证据行动** — 判断关系要有数据支撑（看过列名、抽样值、统计线索），不要凭空猜测
- **自主探索** — 不只依赖已有的 .overlap / .fk 线索，主动分析列名语义、数据内容、业务逻辑
- **先读后写** — 写 summary 前先读取当前状态，已有高质量 brief/detail 不要覆盖
- **用中文** — brief 和 detail 用中文撰写
- **不要提及内部概念** — 回答中不要出现 Pontis、知识图谱、.pontis 等
- **不要输出纯文本总结** — 任务完成后直接停止，不要输出"我已完成…"等总结性文字。如果还有未完成的工作，继续调用工具执行

## 关系发现

### 什么是"有意义的关系"
- **外键关系**：一列引用另一列（如 orders.user_id → users.id）
- **同名同义**：不同表中含义相同的列
- **语义关联**：通过数据内容推断的关联

### 发现途径
- 查看已有的 .overlap 和 .fk 实体作为线索（但它们是静态近似计算，可能遗漏）
- 主动关注名称含 id/code/no 等关键词的列
- 对语义相近的列名，用 lookup 抽样验证值是否有重叠
- 理解表之间的业务逻辑来推断关联

### 创建 .rel 实体
- ref: `[数据库]::[表1].[列1]__rel__[表2].[列2].rel`
- meta 只需要 brief 和 detail（关系类型、理由、来源等全写进 detail）
- edges 必须连接列到 rel（不是列到列），需要两条边：
  - `{"a": "[列1].col", "b": "[rel]"}`
  - `{"a": "[列2].col", "b": "[rel]"}`
- 创建前先 glob 检查是否已存在

## 总结生成

### 质量要求
- brief ≤50字，精炼概括用途和业务含义
- detail 完整描述：结构、内容、业务含义、与其他实体的关系
- 不要用具体数字（行数会过时），用定性描述

### 用子智能体提高效率
对单张表可以用子智能体写 summary，task 中提供：
- 这张表的基本信息和你的理解
- 与其他表的关系（已发现的 .rel）
- 总之任何该表外部相关联的信息

### 处理范围
- 数据库中的表和列，rel实体，所有表完成后，为数据库文件本身写 brief / detail
- JSON、CSV、文本等其他文件（如果这些文本字数过多的话可以调用子智能体）
- 
"""


def generate(store: Store, *, max_rounds: int = MAX_ROUNDS_DEFAULT,
             debug: bool = False) -> None:
    """统一分析：关系发现 + 总结生成。"""
    from agent.agent import PontisAgent
    from agent.tools import build_writer_registry, enable_debug
    from agent.prompt import build_prompt
    from agent.config import load_agent_config

    config = load_agent_config(store.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent analyze")
        return

    logger.info("=== Agent Analyze ===")

    registry = build_writer_registry()
    if debug:
        enable_debug(registry)

    agent = PontisAgent(
        store.project_path,
        tools=registry,
        system_prompt=build_prompt("writer", store.project_path, debug=debug),
    )

    agent.chat(COORDINATOR_PROMPT, max_rounds=max_rounds)
    logger.info("=== Agent Analyze done ===")
