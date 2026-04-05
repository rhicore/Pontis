"""Base schema for all Pontis nodes - FLAT STRUCTURE ONLY"""
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
import enum


class NodeType(str, enum.Enum):
    """All supported node types in Pontis VFS"""
    DIRECTORY = "Directory"
    DB = "DB"
    TABLE = "Table"
    VIEW = "View"
    COLUMN = "Column"
    JSON = "JSON"
    CSV = "CSV"
    MARKDOWN = "Markdown"


class DataType(str, enum.Enum):
    """Data types for columns"""
    INTEGER = "INTEGER"
    REAL = "REAL"
    TEXT = "TEXT"
    BLOB = "BLOB"
    BOOLEAN = "BOOLEAN"
    DATETIME = "DATETIME"
    JSON = "JSON"
    VARCHAR = "VARCHAR"
    UNKNOWN = "UNKNOWN"


class JsonValueType(str, enum.Enum):
    """JSON value types"""
    DICT = "DICT"
    LIST = "LIST"
    STR = "STR"
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    NULL = "NULL"


class BaseNode(BaseModel):
    """Base model for all Pontis nodes - ALL FIELDS ARE FLAT

    IMPORTANT: _meta.yml must contain ONLY flat key-value pairs.
    No nested dicts, no lists. Any nested structure becomes a subfolder.

    Example _meta.yml:
        type: "DB"
        name: "mydb.db"
        dialect: "SQLite"
        table_count: 5
        # Note: tables are subfolders, not a list here
    """
    type: NodeType
    name: str
    description: Optional[str] = None

    # Timestamps are OK - they're scalars
    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)

    @field_validator('*', mode='before')
    @classmethod
    def reject_nested_structures(cls, v, info):
        """Reject nested dicts and lists at validation time"""
        if isinstance(v, dict):
            raise ValueError(
                f"Field '{info.field_name}' contains a nested dict. "
                f"Nested structures must be stored as subfolders, not in _meta.yml"
            )
        if isinstance(v, list):
            # Special case: empty lists are OK (they just mean no items)
            # Non-empty lists should be stored as subfolders
            if v:
                raise ValueError(
                    f"Field '{info.field_name}' contains a list. "
                    f"Lists must be stored as subfolders with _bin files, not in _meta.yml"
                )
        return v

    def to_flat_dict(self) -> Dict[str, Any]:
        """Export to flat dictionary for _meta.yml storage"""
        # Exclude None values to keep _meta.yml clean
        return self.model_dump(exclude_none=True, mode='json')

    class Config:
        extra = "allow"  # Allow additional flat fields
