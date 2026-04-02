"""CSV file node schema - flattened"""
from common.schemas.base import BaseNode, NodeType


class CSVNode(BaseNode):
    """Schema for CSV file nodes - flattened stats"""
    type: NodeType = NodeType.CSV

    # Flat stats
    row_count: int = 0
    column_count: int = 0
    delimiter: str = ","
    has_header: bool = True
    encoding: str = "utf-8"
