"""Table and View node schema - FLAT STRUCTURE"""
from typing import Optional
from common.schemas.base import BaseNode, NodeType


class TableNode(BaseNode):
    """Schema for physical Table nodes - ALL FIELDS ARE FLAT

    Stored as: [dbname].db/[table].table/_meta.yml

    Example:
        type: "Table"
        name: "users.table"
        row_count: 1000
        column_count: 5
        primary_key: "id"
        # Columns are subfolders: users.table/[col].[type].col/
    """
    type: NodeType = NodeType.TABLE

    # Stats (scalars)
    row_count: Optional[int] = None
    column_count: int = 0

    # Primary key (scalar)
    primary_key: Optional[str] = None

    # AI-generated semantic descriptions (scalars)
    brief: Optional[str] = None  # Short summary for ls display (max 20 words)
    detail: Optional[str] = None  # Detailed description for SQL generation guidance

    # Note: Joins are stored as relationship files in the folder, not in _meta.yml
    # Format: [source_col]__to__[target_table].[target_col].rel


class ViewNode(BaseNode):
    """Schema for View nodes - ALL FIELDS ARE FLAT

    Stored as: [dbname].db/[view].view/_meta.yml
    """
    type: NodeType = NodeType.VIEW

    # Stats (scalars)
    row_count: Optional[int] = None
    column_count: int = 0

    # Primary key (scalar)
    primary_key: Optional[str] = None

    # View definition SQL (scalar, can be multi-line string)
    view_definition: Optional[str] = None

    # AI-generated semantic descriptions (scalars)
    brief: Optional[str] = None
    detail: Optional[str] = None

    # Note: base_tables are inferred from view_definition, not stored as a list
