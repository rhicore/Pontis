"""JSON file node schema - simplified"""
from typing import List, Optional
from pydantic import Field
from common.schemas.base import BaseNode, NodeType


class JsonNode(BaseNode):
    """Schema for JSON file nodes - simplified version"""
    type: NodeType = NodeType.JSON

    # Basic info
    node_type: str = "JSON"
    record_count: int = 0
    is_array: bool = False
    top_level_keys: List[str] = Field(default_factory=list)
