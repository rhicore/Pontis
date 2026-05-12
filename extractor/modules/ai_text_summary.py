"""AI Text Summary - 文本文件 AI 总结生成器

职责：
- 匹配 *.md, *.txt, *.log, *.sql 等文本文件节点
- 读取文件统计信息和内容片段
- 使用 LLM 生成 detail 和 brief
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_text_summary ./my_data
"""
import logging
from storage.workspace import Workspace
from extractor.modules.utils.loader import load_config
from extractor.modules.utils.ai_utils import generate_detail_and_brief
from extractor.modules.utils.src import file_exists, get_file_path
from storage.stores.text import is_text_file

logger = logging.getLogger(__name__)


def generate(workspace: Workspace) -> None:
    logger.info("=== AI: Text file summary ===")

    llm = load_config().get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    rows = workspace.cypher("MATCH (n:file) RETURN n")
    seen = set()
    for row in rows:
        meta = row.get("n", {}) or {}
        path = meta.get("name", "")
        basename = path.rsplit("/", 1)[-1]
        if not path or path in seen or not is_text_file(basename):
            continue
        seen.add(path)
        try:
            _generate_for_text(path, workspace, llm)
        except Exception as e:
            logger.warning(f"Failed for {path}: {e}")


def _generate_for_text(path: str, workspace: Workspace, llm) -> bool:
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": path})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    # 读取文件前几行作为内容预览
    source_rel = meta.get("path", "")
    if not source_rel:
        return False

    source_path = get_file_path(workspace, source_rel)
    if not source_path or not file_exists(workspace, source_rel):
        return False

    preview = _read_preview(source_path, max_lines=30)
    char_count = meta.get("char_count", 0)
    line_count = meta.get("line_count", 0)

    prompt = _build_prompt(path, preview, char_count, line_count)

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


def _read_preview(file_path: str, max_lines: int = 30) -> str:
    """读取文件前几行作为预览"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line.rstrip())
            return "\n".join(lines)
    except Exception:
        return ""


def _build_prompt(filename: str, preview: str, char_count: int, line_count: int) -> str:
    prompt = f"""Analyze this text file and generate TWO summaries.

Filename: {filename}
Size: {char_count} chars, {line_count} lines

Content Preview:
{preview[:1000]}

Generate a comprehensive description of what this file contains. Be as detailed as possible — cover the topic, structure, key sections, important data points, and anything notable.

IMPORTANT rules:
- Do NOT mention specific counts (exact line counts, exact character counts). These numbers change frequently and make the summary outdated quickly. Use qualitative language instead (e.g., "a lengthy document", "a short config file").
- Focus on the content, purpose, and semantics — things that remain stable over time.
- Output ONLY plain text, no labels, no markdown formatting.\
"""
    return prompt
