"""Table and View node schema"""
from typing import List, Optional, Dict, Any
from pydantic import Field
from common.schemas.base import BaseNode, NodeType


class TableNode(BaseNode):
    """Schema for physical Table nodes"""
    type: NodeType = NodeType.TABLE

    # Stats - flat structure
    row_count: Optional[int] = None
    column_count: int = 0

    # Primary key information (optional)
    primary_key: Optional[str] = None

    # Join info as flat list of dicts
    joins: List[Dict[str, Any]] = Field(default_factory=list)


class ViewNode(BaseNode):
    """Schema for View nodes - separate from TableNode"""
    type: NodeType = NodeType.VIEW

    # Stats - flat structure
    row_count: Optional[int] = None
    column_count: int = 0

    # Primary key information (optional)
    primary_key: Optional[str] = None

    # View-specific: base tables this view depends on
    base_tables: List[str] = Field(default_factory=list)

    # View definition SQL (optional)
    view_definition: Optional[str] = None

    # Join info as flat list of dicts
    joins: List[Dict[str, Any]] = Field(default_factory=list)
