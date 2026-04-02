"""Markdown file node schema - simplified"""
from typing import Optional
from common.schemas.base import BaseNode, NodeType


class MarkdownNode(BaseNode):
    """Schema for Markdown document nodes - flattened"""
    type: NodeType = NodeType.MARKDOWN

    # Flat stats - no nested structures
    line_count: int = 0
    char_count: int = 0
    word_count: Optional[int] = None
    heading_count: int = 0
    code_block_count: int = 0
    link_count: int = 0
    image_count: int = 0

    # First paragraph (for quick preview)
    first_paragraph: Optional[str] = None
