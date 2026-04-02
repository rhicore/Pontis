"""Column node schema - simplified and flattened"""
from typing import List, Optional, Any
from pydantic import Field
from common.schemas.base import BaseNode, NodeType


class ColumnNode(BaseNode):
    """Schema for Column nodes - flattened stats"""
    type: NodeType = NodeType.COLUMN

    # Column data type for display (e.g., "TEXT", "INTEGER", "REAL")
    # This is shown in ls output
    data_type: str = "UNKNOWN"

    # Nullable flag
    nullable: bool = True

    # Flat stats - all at top level, no nested structures
    cardinality: Optional[int] = None  # Distinct count (shown in ls as "Distinct: X")
    null_count: Optional[int] = None
    null_percentage: Optional[float] = None

    # For numeric columns - flat stats
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    mean_value: Optional[float] = None

    # For string columns - flat stats
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    avg_length: Optional[float] = None

    # Top K most frequent values - compact string format
    # Format: "value1:count1, value2:count2, ..."
    top_k: str = ""

    # Sample values - compact string format
    # Format: "value1, value2, ..."
    samples: str = ""

    # Note: Foreign key info is now handled at Table level by JoinRelationEnricher
