"""Markdown extractor - Phase 1, single-node only"""
import os
import re
import logging
from typing import List, Optional

from extractor.base import BaseExtractor
from extractor.config import ExtractorConfig
from common.schemas.markdown import MarkdownNode

logger = logging.getLogger(__name__)


class MarkdownExtractor(BaseExtractor):
    """
    Extracts Markdown file metadata.
    Rules:
    - Only extracts structural stats (line count, headings, links, etc.)
    - Does NOT generate AI summaries (that's Phase 2)
    """

    @property
    def handles_types(self) -> List[str]:
        return ["Markdown"]

    def can_extract(self, path: str) -> bool:
        """Check if file is Markdown"""
        if not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in self.config.md_extensions

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[MarkdownNode]:
        """Extract Markdown metadata only"""
        try:
            name = os.path.basename(path)

            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            lines = content.split('\n')
            line_count = len(lines)
            char_count = len(content)

            # Count elements
            heading_count = len(re.findall(r'^#{1,6}\s', content, re.MULTILINE))
            code_block_count = len(re.findall(r'^```', content, re.MULTILINE)) // 2
            link_count = len(re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content))
            image_count = len(re.findall(r'!\[([^\]]*)\]\(([^)]+)\)', content))

            # Word count (rough)
            word_count = len(content.split())

            # First paragraph (for preview)
            first_para = self._extract_first_paragraph(content)

            return MarkdownNode(
                name=name,
                line_count=line_count,
                char_count=char_count,
                word_count=word_count,
                heading_count=heading_count,
                code_block_count=code_block_count,
                link_count=link_count,
                image_count=image_count,
                first_paragraph=first_para[:500] if first_para else None
            )

        except Exception as e:
            logger.error(f"Failed to extract Markdown {path}: {e}")
            return None

    def _extract_first_paragraph(self, content: str) -> Optional[str]:
        """Extract first non-empty paragraph"""
        # Remove code blocks
        content = re.sub(r'```[\s\S]*?```', '', content)
        # Remove headings
        content = re.sub(r'^#+.*$', '', content, flags=re.MULTILINE)
        # Find first non-empty line
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('```'):
                return line
        return None
