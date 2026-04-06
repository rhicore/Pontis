"""
Virtual File System for serialized files (JSON, YAML, etc.)

Implements the Container-Root Model where serialized files are navigable
as virtual directories with ROOT.DICT or ROOT.ARRAY as entry points.
"""
from enum import Enum
from typing import Dict, List, Optional, Any, Iterator
from dataclasses import dataclass, field
import json
import os


class JsonNodeType(Enum):
    """JSON node types with file extensions"""
    DICT = "DICT"
    LIST = "LIST"
    STR = "STR"
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    NULL = "NULL"

    @classmethod
    def from_value(cls, value: Any) -> "JsonNodeType":
        """Infer type from Python value"""
        if value is None:
            return cls.NULL
        if isinstance(value, bool):
            return cls.BOOL
        if isinstance(value, int):
            return cls.INT
        if isinstance(value, float):
            return cls.FLOAT
        if isinstance(value, str):
            return cls.STR
        if isinstance(value, list):
            return cls.LIST
        if isinstance(value, dict):
            return cls.DICT
        return cls.STR  # fallback

    def is_scalar(self) -> bool:
        """Check if type is scalar (not container)"""
        return self in (self.STR, self.INT, self.FLOAT, self.BOOL, self.NULL)

    def is_container(self) -> bool:
        """Check if type is container (has children)"""
        return self in (self.DICT, self.LIST)


@dataclass
class SerializedNode:
    """
    Represents a node in serialized file's virtual file system.
    Compatible with VFSNode interface for reuse by glob/grep.
    """
    name: str
    path: str  # Full virtual path (e.g., "data.json/ROOT.DICT/users.LIST")
    node_type: JsonNodeType
    value: Any = field(repr=False)  # Actual JSON value (lazy loaded for large files)
    parent: Optional["SerializedNode"] = field(default=None, repr=False)

    # Metadata for display
    brief: Optional[str] = None  # AI-generated brief description

    @property
    def has_children(self) -> bool:
        """Returns True if node is a container (DICT or LIST)"""
        return self.node_type.is_container()

    @property
    def has_sub(self) -> str:
        """Return [+] for containers, [ ] for scalars"""
        return "[+]" if self.has_children else "[ ]"

    @property
    def display_name(self) -> str:
        """Return formatted name with type suffix (e.g., 'users.DICT')"""
        # Avoid double suffix for ROOT node (e.g., ROOT.DICT.DICT)
        if self.name.endswith(f".{self.node_type.value}"):
            return self.name
        return f"{self.name}.{self.node_type.value}"

    def get_info(self) -> str:
        """
        Generate [Info] field content based on node type.
        Uses string template for consistent formatting.
        """
        # Import here to avoid circular import
        from tool_use.utils.ls_config import format_serialized_info

        type_name = self.node_type.value
        return format_serialized_info(type_name, self.value, max_str_len=30)

    def get_content(self) -> str:
        """
        Get content as string for grep operations.
        For scalars: returns the string value.
        For containers: returns empty string (grep should recurse into children).
        """
        if self.node_type.is_scalar():
            return str(self.value) if self.value is not None else "null"
        return ""

    def list_children(self, offset: int = 0, limit: int = 100) -> List["SerializedNode"]:
        """
        List children with pagination support.

        Args:
            offset: Start index (for LIST type)
            limit: Maximum number of children to return (default 100)

        Returns:
            List of child SerializedNode objects
        """
        if not self.has_children:
            return []

        children = []

        if self.node_type == JsonNodeType.DICT and isinstance(self.value, dict):
            items = list(self.value.items())
            for key, val in items[offset:offset + limit]:
                # Escape special characters in key for path safety
                safe_key = self._escape_key(str(key))
                child_path = f"{self.path}/{safe_key}"
                children.append(SerializedNode(
                    name=safe_key,
                    path=child_path,
                    node_type=JsonNodeType.from_value(val),
                    value=val,
                    parent=self
                ))

        elif self.node_type == JsonNodeType.LIST and isinstance(self.value, list):
            start = offset
            end = min(offset + limit, len(self.value))
            for idx in range(start, end):
                val = self.value[idx]
                child_path = f"{self.path}/{idx}"
                children.append(SerializedNode(
                    name=str(idx),
                    path=child_path,
                    node_type=JsonNodeType.from_value(val),
                    value=val,
                    parent=self
                ))

        return children

    def get_child(self, name: str) -> Optional["SerializedNode"]:
        """Get a specific child by name/index"""
        if not self.has_children:
            return None

        if self.node_type == JsonNodeType.DICT and isinstance(self.value, dict):
            # Try to find by original key (unescape if needed)
            for key, val in self.value.items():
                if self._escape_key(str(key)) == name or str(key) == name:
                    return SerializedNode(
                        name=name,
                        path=f"{self.path}/{name}",
                        node_type=JsonNodeType.from_value(val),
                        value=val,
                        parent=self
                    )

        elif self.node_type == JsonNodeType.LIST and isinstance(self.value, list):
            try:
                idx = int(name)
                if 0 <= idx < len(self.value):
                    return SerializedNode(
                        name=name,
                        path=f"{self.path}/{name}",
                        node_type=JsonNodeType.from_value(self.value[idx]),
                        value=self.value[idx],
                        parent=self
                    )
            except ValueError:
                pass

        return None

    def _escape_key(self, key: str) -> str:
        """Escape special characters in keys for safe path usage"""
        # Replace / with %2F and \ with %5C
        return key.replace("/", "%2F").replace("\\", "%5C")

    @staticmethod
    def unescape_key(key: str) -> str:
        """Unescape special characters in keys"""
        return key.replace("%2F", "/").replace("%5C", "\\")


class SerializedVFSEngine:
    """
    Engine for navigating serialized files (JSON, YAML, etc.) as virtual directories.

    Implements the Container-Root Model:
    - File root: data.json -> virtual entry point
    - Entry node: ROOT.DICT or ROOT.LIST (depending on JSON root type)
    - Navigation: data.json/ROOT.DICT/users.LIST/0.DICT/name.STR
    """

    SUPPORTED_EXTENSIONS = {'.json', '.yaml', '.yml'}

    def __init__(self, file_path: str, file_type: str = None):
        """
        Initialize with a serialized file path.

        Args:
            file_path: Path to the JSON/YAML file
            file_type: Optional file type hint ('.json', '.yaml', '.yml') for files without extension
        """
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        self._file_type = file_type  # Explicit file type hint
        self._data: Optional[Any] = None
        self._loaded = False

    def _load(self) -> Any:
        """Lazy load the file content"""
        if not self._loaded:
            # Use explicit file type hint if available, otherwise detect from extension
            ext = self._file_type or os.path.splitext(self.file_path)[1].lower()
            with open(self.file_path, 'r', encoding='utf-8') as f:
                if ext in ('.json',):
                    self._data = json.load(f)
                elif ext in ('.yaml', '.yml'):
                    import yaml
                    self._data = yaml.safe_load(f)
                else:
                    raise ValueError(f"Unsupported file format: {ext}")
            self._loaded = True
        return self._data

    @property
    def root_type(self) -> JsonNodeType:
        """Get the root node type of the serialized file"""
        data = self._load()
        return JsonNodeType.from_value(data)

    def get_root_node(self) -> SerializedNode:
        """
        Get the ROOT node (entry point for the virtual filesystem).

        Returns:
            SerializedNode with name="ROOT" and appropriate type suffix
        """
        data = self._load()
        root_type = JsonNodeType.from_value(data)
        return SerializedNode(
            name=f"ROOT.{root_type.value}",
            path=f"{self.file_name}/ROOT.{root_type.value}",
            node_type=root_type,
            value=data,
            parent=None
        )

    def resolve_path(self, virtual_path: str) -> Optional[SerializedNode]:
        """
        Resolve a virtual path to a SerializedNode.

        Path format: data.json/ROOT.DICT/users.LIST/0.DICT/name.STR
        Supports: . (current), .. (parent)

        Args:
            virtual_path: The virtual path to resolve

        Returns:
            SerializedNode if found, None otherwise
        """
        # Remove file name prefix if present
        if virtual_path.startswith(self.file_name):
            virtual_path = virtual_path[len(self.file_name):].lstrip('/')

        # Handle special paths
        if not virtual_path or virtual_path == ".":
            return self.get_root_node()

        parts = virtual_path.split('/')
        current = self.get_root_node()

        for part in parts:
            if part == "." or not part:
                continue
            if part == "..":
                if current.parent:
                    current = current.parent
                continue

            # Skip if part matches current node's name (handles ROOT.DICT in path)
            if part == current.name:
                continue

            # Remove type suffix from part if present (e.g., "users.LIST" -> "users")
            clean_part = self._strip_type_suffix(part)

            child = current.get_child(clean_part)
            if child is None:
                return None
            current = child

        return current

    def list_directory(
        self,
        virtual_path: str = "",
        offset: int = 0,
        limit: int = 100
    ) -> List[SerializedNode]:
        """
        List directory contents with pagination.

        Args:
            virtual_path: Path within the serialized file (e.g., "ROOT.DICT/users")
            offset: Start index for LIST pagination
            limit: Maximum items to return

        Returns:
            List of SerializedNode children
        """
        node = self.resolve_path(virtual_path)
        if node is None:
            raise FileNotFoundError(f"Path not found: {virtual_path}")

        if not node.has_children:
            raise NotADirectoryError(f"Not a directory: {virtual_path}")

        return node.list_children(offset=offset, limit=limit)

    def walk(
        self,
        virtual_path: str = "",
        max_depth: int = -1
    ) -> Iterator[SerializedNode]:
        """
        Walk the virtual directory tree (for glob/grep operations).

        Args:
            virtual_path: Starting path
            max_depth: Maximum depth to traverse (-1 for unlimited)

        Yields:
            SerializedNode objects in depth-first order
        """
        node = self.resolve_path(virtual_path)
        if node is None:
            return

        yield node

        if max_depth == 0 or not node.has_children:
            return

        for child in node.list_children(limit=10000):  # Large limit for walk
            yield from self._walk_recursive(child, max_depth - 1)

    def _walk_recursive(
        self,
        node: SerializedNode,
        remaining_depth: int
    ) -> Iterator[SerializedNode]:
        """Recursive helper for walk()"""
        yield node

        if remaining_depth == 0 or not node.has_children:
            return

        for child in node.list_children(limit=10000):
            yield from self._walk_recursive(child, remaining_depth - 1)

    def _strip_type_suffix(self, name: str) -> str:
        """Remove type suffix from name (e.g., 'users.DICT' -> 'users')"""
        for node_type in JsonNodeType:
            suffix = f".{node_type.value}"
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return name


class SerializedNodeFormatter:
    """
    Formatter for SerializedNode display output.
    Decouples data extraction from table rendering.
    """

    @staticmethod
    def format_ls_table(nodes: List[SerializedNode]) -> str:
        """
        Format nodes as ls table with columns: [HasSub] | [Name].[TYPE] | [Info] | [Brief]

        Args:
            nodes: List of SerializedNode to format

        Returns:
            Formatted table string
        """
        if not nodes:
            return "(empty)"

        lines = []
        # Header
        lines.append("[HasSub] | [Name]                | [Info]               | [Brief]")
        lines.append("-" * 75)

        # Rows
        for node in nodes:
            has_sub = node.has_sub
            name = node.display_name[:20]  # Limit name length
            info = node.get_info()[:20]    # Limit info length
            brief = (node.brief or "")[:25]  # Limit brief length

            line = f"{has_sub:<8} | {name:<21} | {info:<20} | {brief}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def format_compact(nodes: List[SerializedNode]) -> List[Dict[str, str]]:
        """
        Format nodes as list of dicts for programmatic use (glob/grep).

        Args:
            nodes: List of SerializedNode

        Returns:
            List of dict with keys: name, type, has_sub, info, path
        """
        return [
            {
                "name": node.name,
                "type": node.node_type.value,
                "has_sub": node.has_sub,
                "info": node.get_info(),
                "path": node.path,
                "is_directory": str(node.has_children),
            }
            for node in nodes
        ]


# Factory for creating appropriate handler based on file extension
def create_serialized_handler(file_path: str) -> Optional[SerializedVFSEngine]:
    """
    Factory function to create handler for serialized files.

    Args:
        file_path: Path to the file

    Returns:
        SerializedVFSEngine if file is supported, None otherwise
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in SerializedVFSEngine.SUPPORTED_EXTENSIONS:
        return SerializedVFSEngine(file_path)
    return None


def is_serialized_file(file_path: str) -> bool:
    """Check if file is a supported serialized format"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SerializedVFSEngine.SUPPORTED_EXTENSIONS