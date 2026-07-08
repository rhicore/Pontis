"""AI JSON Summary - JSON 文件 AI 总结生成器

职责：
- 匹配所有 *.json 节点
- 读取结构信息
- 使用 LLM 生成 detail 和 brief
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_json_summary ./my_data
"""
import os
import logging
from storage.workspace import Workspace
from extractor.utils.loader import load_config
from extractor.utils.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)


def generate(workspace: Workspace) -> None:
    logger.info("=== AI: JSON summary ===")

    llm = load_config().get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    json_rows = workspace.cypher("MATCH (n) WHERE n.name ENDS WITH '.json' RETURN n")
    for row in json_rows:
        path = row["n"]["name"]
        try:
            _generate_for_json(path, workspace, llm)
        except Exception as e:
            logger.warning(f"Failed for {path}: {e}")


def _generate_for_json(path: str, workspace: Workspace, llm) -> bool:
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": path})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    structure_type = meta.get("structure_type", "unknown")
    top_keys = meta.get("top_level_keys", [])
    key_count = meta.get("key_count", 0)
    array_length = meta.get("array_length", 0)

    prompt = f"""Analyze this JSON file and generate TWO summaries.

Structure Type: {structure_type}
"""
    if top_keys:
        keys_str = ", ".join(top_keys[:10])
        prompt += f"Top Level Keys ({key_count} total): {keys_str}\n"
    if array_length:
        prompt += f"Array Length: {array_length} items\n"

    prompt += """
Generate a comprehensive description of what this JSON file represents. Be as detailed as possible — describe the data structure, purpose, each key's role, data patterns, and anything notable.

IMPORTANT rules:
- Do NOT mention specific counts (exact key counts, exact array lengths, exact line counts). These numbers change frequently and make the summary outdated quickly. Use qualitative language instead (e.g., "a handful of top-level keys", "a large array of records").
- Focus on the structure, purpose, and semantics of the data — things that remain stable over time.
- Output ONLY plain text, no labels, no markdown formatting.\
"""

    try:
        detail, brief = generate_detail_and_brief(llm, prompt)
        updates = {}
        if detail:
            updates["detail"] = detail
        if brief:
            updates["brief"] = brief

        if updates:
            workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": path, "props": updates})
            logger.info(f"  AI summary: {path}")
            return True
    except Exception as e:
        logger.debug(f"LLM failed: {e}")

    return False
