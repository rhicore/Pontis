"""Agent Summary Review — Agent 自由探索项目，生成/优化所有实体的 AI 总结

agent 自己发现文件和实体，自主探索和总结。Python 只负责创建 agent 和安全网。

独立执行：
    python -m extractor.modules.agent_summary ./my_data
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

MAX_ROUNDS_DEFAULT = 80

PROMPT = """\
你的任务是为项目中所有缺少或需要改进 summary 的实体补充 brief 和 detail。

策略：你是协调者。先建立对每个数据库的全局认知，再把具体的深度探索+写 summary \
工作分配给子智能体（agent 工具）。小文件（JSON/文本）你自己直接处理。

工作流程：

1. 发现项目结构
   用 glob '*.db', '*.sqlite', '*.sqlite3', '*.duckdb' 找数据库，
   用 glob '*.json', '*.txt', '*.md', '*.log', '*.sql', '*.py' 等找其他文件。

2. 对每个数据库——先探索表面结构
   a. glob 查看所有表和视图
   b. meta 查看每张表的基本信息（行数、列数、主键）
   c. 查看是否有 .overlap 或 .rel 实体，了解表间关系
   d. 把这些信息记在心里，形成对这个数据库的全局理解

3. 分配子智能体写 summary
   对数据库中的每个表/视图，启动一个子智能体：
   - task 中必须包含：数据库名称和整体结构（你刚才探索到的）、这张表的基本信息、 \
     与其他表的关系（如外键、overlap）、写 summary 的要求
   - 子智能体会自己去深入探索这张表的列数据、统计信息，然后写 brief 和 detail
   - max_rounds 设为 8-10 即可
   - description 填表名，方便日志追踪

4. 所有表的 summary 完成后，你自己为数据库文件写 detail（用 update_meta）

5. 对 JSON 和文本文件：不需要子智能体，你自己读取内容并写 brief 和 detail

质量要求：
- brief ≤50字，精炼概括用途
- detail 完整描述结构、内容、关系、业务含义
- 不要用具体数字（行数、数量等会过时的信息），用定性描述
- 如果已有 brief/detail 且质量不错，不要覆盖；如果明显不足则补充修正
- 用中文
- 不要提及 Pontis、知识图谱、.pontis 等内部概念
"""


def generate(store: Store, *, max_rounds: int = MAX_ROUNDS_DEFAULT) -> None:
    """为所有实体生成/优化 AI 总结。

    Args:
        store: Store 实例
        max_rounds: agent 最大 tool call 轮次
    """
    from agent.agent import PontisAgent
    from agent.tools import build_writer_registry
    from agent.prompt import build_prompt
    from agent.config import load_agent_config

    config = load_agent_config(store.project_path)
    if not config.api_key:
        logger.warning("Agent not configured (no API key), skipping agent summary")
        return

    logger.info("=== Agent Summary Review ===")

    agent = PontisAgent(
        store.project_path,
        tools=build_writer_registry(),
        system_prompt=build_prompt("writer", store.project_path),
    )

    agent.chat(PROMPT, max_rounds=max_rounds)
    logger.info("=== Agent Summary Review done ===")
