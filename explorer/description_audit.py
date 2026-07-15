"""Agent Description Audit — final metadata review before README."""
import logging

from explorer.utils.description_contract import DESCRIPTION_CONTRACT
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = f"""\
你是数据库业务词典的最终编辑。当前所有 `table/col` 已经有 brief/detail。你的任务是逐项审查并把它们统一到同一个 description 契约。

{DESCRIPTION_CONTRACT}

## 审查方式

- 用 `find` 列出全部 table/col。
- 用 `meta(..., property=["official_column_description","official_value_description","brief","detail"])` 定向读取定义。
- 已符合契约的实体保持不变；不符合契约的实体用 `update_meta` 重写 brief/detail。
- 完成后直接停止。

## 完成条件

- 所有 table/col 的 description 都是业务词典定义，并与 official metadata 一致。
"""


def generate(workspace: Workspace) -> dict:
    """Audit generated descriptions after relation/disambiguation review."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.bird_metadata import official_metadata_note
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping description audit")
        return {}

    logger.info("=== Agent Description Audit ===")

    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "update_meta"],
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT + official_metadata_note(workspace.project_path))
    logger.info("=== Agent Description Audit done ===")
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
