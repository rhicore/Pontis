"""Column node schema - FLAT STRUCTURE ONLY

Columns are stored as: [table].table/[colname].[datatype].col/

Inside the .col folder:
- _meta.yml: flat metadata (stats, type info)
- .sample/ folder: sample values (with _bin cache)
- .topk/ folder: top-k frequent values (with _bin cache)
"""
from typing import Optional, Any
from common.schemas.base import BaseNode, NodeType


class ColumnNode(BaseNode):
    """Schema for Column nodes - ALL FIELDS ARE FLAT

    Stored as: [table].table/[colname].[datatype].col/_meta.yml

    Example _meta.yml:
        type: "Column"
        name: "id.INT.col"
        data_type: "INT"
        nullable: false
        cardinality: 1000
        null_count: 0
        min_value: 1
        max_value: 1000
        # Note: samples and top_k are NOT stored here
        # They are in subfolders: .sample/ and .topk/
    """
    type: NodeType = NodeType.COLUMN

    # Column data type for display (scalar)
    data_type: str = "UNKNOWN"

    # Nullable flag (scalar)
    nullable: bool = True

    # Stats (all scalars)
    cardinality: Optional[int] = None  # Distinct count
    null_count: Optional[int] = None
    null_percentage: Optional[float] = None

    # For numeric columns (scalars)
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    mean_value: Optional[float] = None

    # For string columns (scalars)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    avg_length: Optional[float] = None

    # AI-generated semantic descriptions (scalars)
    brief: Optional[str] = None
    detail: Optional[str] = None

    # Note: samples and top_k are stored in subfolders, not here
    # - .sample/_meta.yml and .sample/_bin
    # - .topk/_meta.yml and .topk/_bin


class SampleNode(BaseNode):
    """Sample values node - stored in .sample/ folder

    The actual sample values are stored in _bin file (serialized).
    _meta.yml only contains metadata about the sample.
    """
    type: NodeType = NodeType.COLUMN  # Same type as parent

    # Sample metadata (scalars)
    sample_count: int = 0
    sample_source: Optional[str] = None  # Reference to parent column

    # Note: actual samples are in _bin file


class TopKNode(BaseNode):
    """Top-K values node - stored in .topk/ folder

    The actual top-k values are stored in _bin file (serialized).
    _meta.yml only contains metadata.
    """
    type: NodeType = NodeType.COLUMN  # Same type as parent

    # Top-K metadata (scalars)
    k: int = 5
    total_distinct: Optional[int] = None

    # Note: actual top-k values are in _bin file
