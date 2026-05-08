"""Agent Analyze — 为所有实体生成 AI 总结（brief/detail）。

唯一职责：深入理解数据，为表、列、关系、文件等所有实体撰写高质量总结。
不负责发现关系（rel）或消歧（disambig），这些由专门的脚本处理。

子智能体用于处理具体的单表/单任务，降低单次对话的复杂度。

独立执行:
    python -m extractor.agent_analyze ./my_data
"""
import logging

from storage.workspace import Workspace
import os

logger = logging.getLogger(__name__)

COORDINATOR_PROMPT = """\
你是数据分析助手，负责深入理解用户的项目数据。

## 你的目标

为项目中所有实体（表、列、关系实体、文件）生成高质量的 brief 和 detail 总结。

## 守则

- **先读后写** — 写 summary 前先读取当前状态。如果已有的 brief/detail 是低质量的（如只包含统计信息、语义描述模糊、有错误），应当改进覆盖；如果已有高质量内容则保留
- **基于证据** — 总结必须基于你实际读取的数据，不要凭空猜测
- **用中文** — brief 和 detail 用中文撰写
- **不要提及内部概念** — 回答中不要出现 Pontis、知识图谱、.pontis 等
- **不要输出纯文本总结** — 任务完成后直接停止，不要输出"我已完成…"等总结性文字

## 总结质量要求

### brief（≤50字）
精炼概括用途和业务含义，不要包含具体数字（行数会过时）。

### detail（完整描述）
包含：结构特征、内容含义、业务用途、与其他实体的关系。
不要包含具体行数，用定性描述（如"大量"、"数百条"）。

### 特别注意：列的总结
列的 detail 是下游 agent 编写 SQL 时的关键参考。必须包含以下信息：

1. **业务含义**：这列在业务上代表什么
2. **值特征**：值的格式、范围、枚举值示例。对于代码型列（如 SOC、DOC、EILCode），如果有 topk 数据，**必须列出关键代码值→含义的映射**，例如：
   > 常见值：69=District Community Day Schools, 66=Community Day Schools, ...
3. **易混淆标注**：如果数据库中存在**名称相似或语义相近**的其他列，用"区别于"句式标注客观差异

### 易混淆标注规范

只描述客观事实差异，**不给操作建议**（如"优先用 X 列"、"应该用代码列"等）。具体怎么选由 agent 自行判断。

示例：
- `SOC` col: "学校运营类型代码（2位数字）。区别于 SOCType（该类型的文字描述，如'District Community Day Schools'）。常见值：69=District Community Day Schools, 66=Community Day Schools"
- `Free Meal Count` col: "免费午餐学生人数。区别于 FRPM Count（免费或减价午餐学生人数，数值通常更大，包含两个指标）"

## 策略：依赖感知的协调者

**理解有先后顺序**。某些表的含义依赖于枢纽表（被大量引用的核心表），\
必须先理解枢纽表，才能正确理解依赖它的表。

## 用子智能体提高效率

对单张表可以用子智能体写 summary，task 中提供：
- 这张表的基本信息和你的理解
- 与其他表的关系（已发现的 rel / fk）
- 如果这张表依赖枢纽表，告诉子智能体枢纽表的 summary

## 处理范围

- 数据库中的表和列
- fk 和 rel 实体
- 所有表完成后，为数据库文件本身写 brief / detail
- JSON、CSV、文本等其他文件（如果文本过长可调用子智能体）

- 如果你在总结过程中发现缺失的关系或歧义，只需在相关实体的 detail 中提及即可，不要创建新实体
"""


def generate(workspace: Workspace) -> None:
    """为所有实体生成 AI 总结。"""
    from agent.config import create_agent, AgentSpec
    from agent.guardrail import build_guardrails
    from agent.utils import load_agent_config

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent analyze")
        return

    logger.info("=== Agent Analyze (summaries only) ===")

    spec = AgentSpec(mode="writer")
    project_name = os.path.basename(os.path.abspath(workspace.project_path))
    spec.projects = [project_name]
    spec.guardrails = build_guardrails(spec, ["round_limit"])
    agent = create_agent(workspace.project_path, spec)

    agent.chat(COORDINATOR_PROMPT)
    logger.info("=== Agent Analyze done ===")
