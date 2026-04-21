"""Agent Join Detection — Agent 分析列关系，创建 join 关系

agent 自己发现 overlap/rel 实体，自主分析并创建 join。Python 只负责创建 agent 和安全网。

独立执行：
    python -m extractor.modules.agent_join_detect ./my_data
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

MAX_ROUNDS_DEFAULT = 30

PROMPT = """\
你的任务：分析项目中数据库列之间的关系，找出高概率的 join 并创建 .join 实体。

工作流程：
1. 用 glob '*.db', '*.sqlite', '*.sqlite3', '*.duckdb' 找到所有数据库文件
2. 对每个数据库：
   a. 用 glob '*.overlap' 和 '*.rel' 查看所有已检测的列关系
   b. 如果没有 overlap/rel 实体，跳过这个数据库
   c. 用 meta 查看每个 overlap/rel 的统计数据（jaccard、coverage 等）
   d. 对于统计数据支持的列对，查看相关列的实际数据来验证语义关联
   e. 先用 glob '*.join' 检查已有的 join，避免重复创建
   f. 对于确认的高概率 join（置信度 >= 0.7），用 create_entity 创建 .join 实体：
      - ref 格式: [数据库文件]::[表1].[列1]__join__[表2].[列2].join
      - meta 中包含: from_table, from_column, to_table, to_column, confidence, reason
      - 写 brief（≤50字）和 detail（完整描述 join 关系）
   g. 置信度低于 0.7 的不要创建

注意：
- confidence 基于数据统计 + 语义理解综合判断
- 用中文写 brief 和 detail
"""


def generate(store: Store, *, max_rounds: int = MAX_ROUNDS_DEFAULT) -> None:
    """分析所有 DB 文件的列关系，创建高置信度的 join。

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
        logger.warning("Agent not configured (no API key), skipping agent join detect")
        return

    logger.info("=== Agent Join Detection ===")

    agent = PontisAgent(
        store.project_path,
        tools=build_writer_registry(),
        system_prompt=build_prompt("writer", store.project_path),
    )

    agent.chat(PROMPT, max_rounds=max_rounds)
    logger.info("=== Agent Join Detection done ===")
