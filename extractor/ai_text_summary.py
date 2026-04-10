"""AI Text Summary - 文本文件 AI 总结生成器

职责：
- 匹配 *.md, *.txt, *.log, *.sql 等文本文件节点
- 读取文件统计信息和内容片段
- 使用 LLM 生成 detail 和 brief
- 追加到 _meta.yml

独立执行：
    python -m extractor.ai_text_summary ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef, get_llm
from extractor.ai_utils import generate_detail_and_brief

logger = logging.getLogger(__name__)

# 支持的文本文件后缀
TEXT_EXTENSIONS = {'.md', '.txt', '.log', '.sql', '.py', '.js', '.ts', '.tsx', '.jsx',
                   '.sh', '.bash', '.zsh', '.yaml', '.yml', '.toml', '.xml', '.html',
                   '.css', '.scss', '.json', '.csv', '.tsv'}


def generate(storage: VFSStorage) -> None:
    logger.info("=== AI: Text file summary ===")

    llm = get_llm()
    if not llm:
        logger.warning("LLM not configured, skipping AI summary")
        return

    # 匹配所有文本类型文件
    for ext in ['.md', '.txt', '.log', '.sql']:
        for node in storage.find_nodes(f"*{ext}"):
            try:
                _generate_for_text(node, storage, llm)
            except Exception as e:
                logger.warning(f"Failed for {node.name}: {e}")


def _generate_for_text(node: NodeRef, storage: VFSStorage, llm) -> bool:
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "detail" in meta and "brief" in meta:
        return False

    # 读取文件前几行作为内容预览
    source_rel = meta.get("path", "")
    if not source_rel:
        return False

    source_path = storage.resolve_path(source_rel)
    if not os.path.exists(source_path):
        return False

    preview = _read_preview(source_path, max_lines=30)
    char_count = meta.get("char_count", 0)
    line_count = meta.get("line_count", 0)

    prompt = _build_prompt(node.name, preview, char_count, line_count)

    try:
        detail, brief = generate_detail_and_brief(llm, prompt, max_tokens=100)
        if detail:
            meta["detail"] = detail
        if brief:
            meta["brief"] = brief

        if detail or brief:
            storage.write_meta(node, meta)
            logger.info(f"  AI summary: {node.rel_path}")
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


def main():
    import argparse, sys
    parser = argparse.ArgumentParser(description="AI text file summary")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    pontis_path = os.path.join(os.path.abspath(args.target), ".pontis")
    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found", file=sys.stderr)
        sys.exit(1)
    generate(VFSStorage(pontis_path))
    print("Done.")


if __name__ == '__main__':
    main()
