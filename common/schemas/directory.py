"""Directory node schema"""
from typing import Optional
from pydantic import Field
from common.schemas.base import BaseNode, NodeType


class DirectoryNode(BaseNode):
    """Schema for Directory nodes"""
    type: NodeType = NodeType.DIRECTORY

    # Stats - flat structure
    child_count: int = 0
    file_count: int = 0
    subdir_count: int = 0
