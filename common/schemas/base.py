"""Base schema for all Pontis nodes"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
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


class Stats(BaseModel):
    """Common statistics for nodes"""
    pass


class TopKElement(BaseModel):
    """Top K element with count"""
    value: Any
    count: int


class BaseNode(BaseModel):
    """Base model for all Pontis nodes"""
    type: NodeType
    name: str
    description: Optional[str] = None
    short_summary: Optional[str] = None
    long_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)

    class Config:
        extra = "allow"
