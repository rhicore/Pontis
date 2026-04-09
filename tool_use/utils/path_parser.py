"""
Unified path::entity pattern parser.

All path_pattern in Pontis use the structure: path::entity
- Left of :: is the physical file glob pattern
- Right of :: is the entity (logical entity) glob pattern
- If no ::, it's a pure physical file pattern (no entity matching)

Examples:
    data/**/*.db                       -> file="data/**/*.db", entity=None
    data/**/*.db::*user*.table         -> file="data/**/*.db", entity="*user*.table"
    src/**/*.json::ROOT.DICT.users     -> file="src/**/*.json", entity="ROOT.DICT.users"
    *                                  -> file="*", entity=None
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedPath:
    """Result of parsing a path::entity pattern."""
    file_pattern: str          # Physical file glob pattern
    entity_pattern: Optional[str] = None  # Entity glob pattern (None if no ::)
    raw: str = ""              # Original input

    @property
    def has_entity(self) -> bool:
        return self.entity_pattern is not None


def parse_path_pattern(pattern: str) -> ParsedPath:
    """
    Parse a path::entity pattern into its components.

    Handles edge cases:
    - No :: -> entity_pattern is None
    - :: with empty right -> entity_pattern is None (treat as file-only)
    - Windows paths (C:\\) should not confuse :: detection
    """
    if '::' not in pattern:
        return ParsedPath(file_pattern=pattern, entity_pattern=None, raw=pattern)

    # Split on first :: only
    parts = pattern.split('::', 1)
    file_pattern = parts[0]
    entity_pattern = parts[1] if parts[1] else None

    return ParsedPath(
        file_pattern=file_pattern,
        entity_pattern=entity_pattern,
        raw=pattern,
    )


@dataclass
class ResolvedTarget:
    """A resolved physical file + optional entity path."""
    file_path: str            # Relative path to physical file
    entity_path: Optional[str] = None  # Entity path within the file (e.g., "users.table")
    display_path: str = ""    # For display: file::entity

    def __post_init__(self):
        if self.entity_path:
            self.display_path = f"{self.file_path}::{self.entity_path}"
        else:
            self.display_path = self.file_path
