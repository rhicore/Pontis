"""PDF Info Generator - PDF文件信息生成器

职责：
- 匹配 *.pdf 节点
- 添加PDF特有元信息（页数、作者、标题、创建日期等）

独立执行：
    python -m extractor.pdf_info ./my_data
"""
import os
import logging
from extractor.utils import VFSStorage, NodeRef

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage) -> None:
    """为所有PDF节点生成信息"""
    logger.info("=== Generating PDF info ===")

    for node in storage.find_nodes("*.pdf"):
        try:
            _generate_for_pdf(node, storage)
        except Exception as e:
            logger.warning(f"Failed to generate info for {node.name}: {e}")


def _generate_for_pdf(node: NodeRef, storage: VFSStorage) -> bool:
    """为单个PDF文件生成信息"""
    meta = storage.read_meta(node)
    if not meta:
        return False

    if "page_count" in meta:
        return False

    rel_path = meta.get("path")
    pdf_path = storage.resolve_path(rel_path) if rel_path else None
    if not pdf_path or not os.path.exists(pdf_path):
        return False

    try:
        info = _extract_pdf_info(pdf_path)

        stat = os.stat(pdf_path)
        info["file_size"] = stat.st_size

        # 更新meta
        meta.update(info)
        storage.write_meta(node, meta)

        logger.info(f"  PDF info: {node.rel_path} ({info.get('page_count', 0)} pages)")
        return True

    except Exception as e:
        logger.debug(f"Could not get PDF info: {e}")
        return False


def _extract_pdf_info(pdf_path: str) -> dict:
    """提取PDF元信息"""
    info = {
        "page_count": 0,
        "title": "",
        "author": "",
        "creator": "",
        "producer": "",
        "creation_date": "",
        "modification_date": "",
    }

    # 尝试 PyPDF2
    try:
        import PyPDF2
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            info["page_count"] = len(reader.pages)

            if reader.metadata:
                meta = reader.metadata
                info["title"] = meta.get('/Title', '') or ''
                info["author"] = meta.get('/Author', '') or ''
                info["creator"] = meta.get('/Creator', '') or ''
                info["producer"] = meta.get('/Producer', '') or ''
                info["creation_date"] = meta.get('/CreationDate', '') or ''
                info["modification_date"] = meta.get('/ModDate', '') or ''

        # 提取文本用于生成摘要
        text_content = ""
        for page in reader.pages[:3]:  # 只取前3页
            text_content += page.extract_text() or ""
        info["sample_text"] = text_content[:1000]  # 前1000字符

        return info
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"PyPDF2 failed: {e}")

    # 尝试 pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            info["page_count"] = len(pdf.pages)

            # 提取文本
            text_content = ""
            for page in pdf.pages[:3]:
                text_content += page.extract_text() or ""
            info["sample_text"] = text_content[:1000]

        return info
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"pdfplumber failed: {e}")

    raise RuntimeError("No PDF library available")


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate PDF info")
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
