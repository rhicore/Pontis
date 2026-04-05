"""PDF Chunk Generator - PDF分片生成器

职责：
- 匹配所有 *.pdf 节点
- 提取PDF文本，按页分片
- 为每页创建 .chunk/ 文件夹
- 写入 _raw 文件（JSON格式的页面文本）

独立执行：
    python -m extractor.pdf_chunk ./my_data
"""
import os
import logging
from typing import List
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有PDF文件生成page chunks"""
    logger.info("=== Generating PDF page chunks ===")

    for node in storage.find_nodes("*.pdf"):
        try:
            _generate_for_pdf(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate chunks for {node.name}: {e}")


def _generate_for_pdf(pdf_node: NodeRef, storage: VFSStorage) -> bool:
    """为单个PDF文件创建page chunks"""
    meta = storage.read_meta(pdf_node)
    if not meta:
        return False

    rel_path = meta.get("path")
    pdf_path = storage.resolve_path(rel_path) if rel_path else None
    if not pdf_path or not os.path.exists(pdf_path):
        return False

    # 尝试提取PDF文本
    try:
        pages = _extract_pdf_pages(pdf_path)
    except Exception as e:
        logger.debug(f"Could not extract PDF: {e}")
        return False

    if not pages:
        return False

    # 为每页创建chunk
    created = 0
    for i, page_text in enumerate(pages):
        chunk_name = f"page_{i+1}.chunk"
        chunk_rel_path = os.path.join(pdf_node.rel_path, chunk_name)
        chunk_node = NodeRef(chunk_rel_path, pdf_node.pontis_root)

        if storage.exists(chunk_node):
            continue

        # 创建chunk文件夹
        storage.ensure_dir(chunk_node.full_path)

        # 写入页面文本到_raw（JSON格式）
        storage.write_raw(chunk_node, {"content": page_text})

        # 写入meta（仅保留必要的字段）
        chunk_meta = {
            "page_number": i + 1,
            "char_count": len(page_text),
            "created_at": __import__('datetime').datetime.now().isoformat(),
        }
        storage.write_meta(chunk_node, chunk_meta)
        created += 1

    logger.info(f"  Created {created} page chunks: {pdf_node.name}")
    return True


def _extract_pdf_pages(pdf_path: str) -> List[str]:
    """提取PDF每页的文本内容"""
    pages = []

    # 尝试使用 PyPDF2
    try:
        import PyPDF2
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text = page.extract_text()
                pages.append(text if text else "")
        return pages
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"PyPDF2 failed: {e}")

    # 尝试使用 pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                pages.append(text if text else "")
        return pages
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"pdfplumber failed: {e}")

    # 如果都失败了，返回空列表
    raise RuntimeError("No PDF library available (install PyPDF2 or pdfplumber)")


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate PDF page chunks")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    storage = VFSStorage(pontis_path)
    generate(storage)
    print("Done.")


if __name__ == '__main__':
    main()
