"""Database node schema - FLAT STRUCTURE"""
from common.schemas.base import BaseNode, NodeType


class DBNode(BaseNode):
    """Schema for Database nodes - ALL FIELDS ARE FLAT

    Stored as: [dbname].db/_meta.yml

    Example:
        type: "DB"
        name: "mydb.db"
        dialect: "SQLite"
        table_count: 5
        view_count: 2
        # Tables are subfolders: mydb.db/[table].table/
    """
    type: NodeType = NodeType.DB

    # Database dialect (scalar)
    dialect: str = "Unknown"

    # Counts (scalars) - shown in ls
    table_count: int = 0
    view_count: int = 0

    # Note: Tables/views are NOT stored as a list here
    # They are stored as subfolders: [table_name].table/
