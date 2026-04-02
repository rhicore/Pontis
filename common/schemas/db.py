"""Database node schema - flattened"""
from common.schemas.base import BaseNode, NodeType


class DBNode(BaseNode):
    """Schema for Database nodes - flattened stats"""
    type: NodeType = NodeType.DB

    # Database dialect
    dialect: str = "Unknown"

    # Flat stats - shown in ls
    table_count: int = 0
    view_count: int = 0
