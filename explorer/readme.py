"""Agent README Writer — create a compact database-level orientation note."""

import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

README_MAX_CHARS = 1800
MAX_REWRITE_ROUNDS = 2


PROMPT = f"""\
你是 Pontis 的数据库概览编辑。当前图谱已经用独立实体和边保存表、列、外键、关系、消歧义和统计。你的任务是创建或更新一个简短的 `README:knowledge`，只提供进入数据库时需要的全局认知。

## README 内容

正文不超过 {README_MAX_CHARS} 个字符，使用以下结构：

1. `# <数据库名>`
2. `## 用途`：一小段说明数据库描述的业务领域和时间/地域范围。
3. `## 核心业务对象`：3–8 条，每条写一个业务对象以及承载它的主要表名。
4. `## 全局注意事项`：最多 5 条，只记录跨多个对象才看得出的粒度、覆盖范围、编码体系或数据质量事实。

README 只承担库级概览。表列说明、行数、字段清单、外键端点、关系清单、空值比例、值样例、消歧义详情和数据字典文件已经能从对应实体、边和 metadata 读取，由相应实体负责。

## 工作方式

- 用 `find` 定位数据库和主要表，用 `meta` 读取已有摘要。
- 全局事实以当前图谱中已有的 official metadata、brief/detail 和关系为依据。
- 使用中文，保留数据库原始表名和业务代码。
- 已有 `README:knowledge` 时用 `update_meta` 重写；不存在时用 `create_entity` 创建，并连接唯一 `db` 节点。
- `brief` 用一句话说明数据库主题；`detail` 写上述精简正文。

完成写入后回复 `DONE`。
"""


def generate(workspace: Workspace) -> dict:
    """Generate a compact README and enforce its size contract."""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec
    from explorer.utils.bird_metadata import official_metadata_note

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping README generation")
        return {}

    logger.info("=== Agent README Writer ===")
    spec = explorer_writer_spec(
        workspace,
        tools=["find", "meta", "create_entity", "update_meta"],
        include_readme=False,
    )
    agent = create_agent(workspace.project_path, spec)
    agent.chat(PROMPT + official_metadata_note(workspace.project_path))

    for round_no in range(MAX_REWRITE_ROUNDS + 1):
        detail = _readme_detail(workspace)
        if detail and len(detail) <= README_MAX_CHARS:
            logger.info("=== Agent README Writer done (%d chars) ===", len(detail))
            return _preprocess_metrics(agent)
        if round_no >= MAX_REWRITE_ROUNDS:
            break
        current_size = len(detail) if detail else 0
        agent.chat(
            f"README 长度校验未通过：当前 {current_size} 字符，上限 {README_MAX_CHARS}。"
            "请按既定四段结构重写完整 detail，压缩为库级概览后回复 DONE。"
        )

    raise RuntimeError(
        f"README 未通过长度校验：需要非空且不超过 {README_MAX_CHARS} 字符"
    )


def _readme_detail(workspace: Workspace) -> str:
    rows = workspace.cypher(
        "MATCH (n:knowledge {name: 'README'}) RETURN n.detail AS detail"
    )
    return str(rows[0].get("detail") or "").strip() if rows else ""


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
