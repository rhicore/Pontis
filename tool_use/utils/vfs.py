"""Virtual File System interface for .pontis metadata - updated for flat schema"""
import os
import yaml
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field

from tool_use.utils.serialized_vfs import (
    SerializedVFSEngine,
    SerializedNode,
    create_serialized_handler,
    is_serialized_file,
)
from tool_use.utils.config import (
    LS_TYPE_CONFIG,
    SERIALIZED_TYPE_CONFIG,
    TypeConfig,
)
from tool_use.utils.formatters import (
    get_type_config,
    format_info_from_meta,
    get_brief_from_meta,
    get_file_type_from_name,
)

@dataclass
class VFSNode:
    """Represents a node in the virtual file system"""
    name: str
    path: str
    node_type: str
    has_children: bool = False
    short_summary: Optional[str] = None
    # Flat stats - directly from meta, not nested
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    child_count: Optional[int] = None
    file_count: Optional[int] = None
    subdir_count: Optional[int] = None
    table_count: Optional[int] = None
    view_count: Optional[int] = None
    line_count: Optional[int] = None
    cardinality: Optional[int] = None
    null_percentage: Optional[float] = None  # For Column nodes
    data_type: Optional[str] = None  # For Column nodes
    raw_meta: Dict[str, Any] = field(default_factory=dict)


class PontisVFS:
    """
    Interface for interacting with .pontis shadow directories.
    Updated for flat schema structure.
    """

    META_FILENAME = "_meta.yml"
    RAW_FILENAME = "_raw"

    def __init__(self, pontis_root: str):
        if not os.path.exists(pontis_root):
            raise ValueError(f".pontis directory not found: {pontis_root}")
        self.pontis_root = os.path.abspath(pontis_root)

    def list_directory(
        self,
        path: str = "",
        offset: int = 0,
        limit: int = 100
    ) -> Union[List[VFSNode], List[SerializedNode]]:
        """
        List contents of a virtual directory.

        Args:
            path: Path to list (relative to pontis_root)
            offset: Start index for pagination (for serialized file LIST nodes)
            limit: Maximum items to return (default 100)

        Returns:
            List of VFSNode (for regular directories) or SerializedNode (for JSON/YAML)
        """
        full_path = os.path.join(self.pontis_root, path)

        # Check if this is a path inside a serialized file
        if self._is_serialized_virtual_path(path):
            return self._list_serialized_path(path, offset=offset, limit=limit)

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Path not found: {path}")
        if not os.path.isdir(full_path):
            raise NotADirectoryError(f"Not a directory: {path}")

        nodes = []
        for entry in os.listdir(full_path):
            # 跳过隐藏文件和内部文件
            if entry.startswith('.') or entry == self.META_FILENAME or entry == self.RAW_FILENAME:
                continue

            entry_path = os.path.join(full_path, entry)
            rel_path = os.path.join(path, entry) if path else entry

            if os.path.isdir(entry_path):
                meta_path = os.path.join(entry_path, self.META_FILENAME)
                raw_path = os.path.join(entry_path, self.RAW_FILENAME)

                # 检查是否有可见内容（除 _meta 和 _raw 外）
                has_visible_children = self._dir_has_visible_children(entry_path)

                # 检查是否是序列化文件目录（有 _raw 且是序列化格式）
                is_serialized_dir = os.path.exists(raw_path) and is_serialized_file(entry)

                if os.path.exists(meta_path):
                    node = self._load_node(entry, rel_path, meta_path)
                    # 序列化文件目录：如果 _raw 存在则有 sub
                    # 普通目录：看是否有可见内容
                    if is_serialized_dir:
                        node.has_children = True  # 序列化文件可以进入
                    else:
                        node.has_children = has_visible_children
                else:
                    node = self._create_directory_node(entry, rel_path, entry_path)
                    node.has_children = has_visible_children
                nodes.append(node)
            elif is_serialized_file(entry_path):
                node = self._create_serialized_node(entry, rel_path, entry_path)
                nodes.append(node)
            else:
                node = self._create_file_node(entry, rel_path, entry_path)
                nodes.append(node)

        return sorted(nodes, key=lambda n: (not n.has_children, n.name.lower()))

    def get_node_info(self, path: str) -> Union[VFSNode, SerializedNode]:
        """Get detailed information about a node."""
        full_path = os.path.join(self.pontis_root, path)

        # Check if this is a serialized virtual path
        if self._is_serialized_virtual_path(path):
            node = self._get_serialized_node(path)
            if node:
                return node
            raise FileNotFoundError(f"Node not found: {path}")

        if os.path.isdir(full_path):
            meta_path = os.path.join(full_path, self.META_FILENAME)
            if os.path.exists(meta_path):
                return self._load_node(os.path.basename(path), path, meta_path)
            else:
                return VFSNode(
                    name=os.path.basename(path),
                    path=path,
                    node_type="Directory",
                    has_children=True
                )

        raise FileNotFoundError(f"Node not found: {path}")

    def search_nodes(self, query: str, path: str = "") -> List[Dict[str, Any]]:
        """Search for nodes matching a query string."""
        results = []
        search_path = os.path.join(self.pontis_root, path)
        query_lower = query.lower()

        for root, dirs, files in os.walk(search_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if file == self.META_FILENAME:
                    meta_path = os.path.join(root, file)
                    rel_path = os.path.relpath(root, self.pontis_root)
                    try:
                        with open(meta_path, 'r') as f:
                            meta = yaml.safe_load(f)
                        name = meta.get('name', '')
                        short_summary = meta.get('short_summary', '')
                        long_summary = meta.get('long_summary', '')
                        node_type = meta.get('type', 'Unknown')

                        if (query_lower in name.lower() or
                            query_lower in short_summary.lower() or
                            query_lower in long_summary.lower() or
                            query_lower in node_type.lower()):
                            results.append({
                                'path': rel_path,
                                'name': name,
                                'type': node_type,
                                'short_summary': short_summary
                            })
                    except Exception:
                        pass

        return results

    def find_nodes(self, pattern: str, path: str = "") -> List[str]:
        """Find nodes by glob pattern."""
        import fnmatch
        search_path = os.path.join(self.pontis_root, path)
        results = []

        for root, dirs, files in os.walk(search_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for dir_name in dirs:
                full_path = os.path.join(root, dir_name)
                rel_path = os.path.relpath(full_path, self.pontis_root)
                if fnmatch.fnmatch(dir_name, pattern) or fnmatch.fnmatch(rel_path, pattern):
                    results.append(rel_path)

        return sorted(results)

    def get_full_metadata(self, path: str) -> Dict[str, Any]:
        """Get complete metadata for a node."""
        full_path = os.path.join(self.pontis_root, path)
        if os.path.isdir(full_path):
            meta_path = os.path.join(full_path, self.META_FILENAME)
            if os.path.exists(meta_path):
                with open(meta_path, 'r') as f:
                    return yaml.safe_load(f)
        raise FileNotFoundError(f"Metadata not found: {path}")

    def _load_node(self, name: str, rel_path: str, meta_path: str) -> VFSNode:
        """Load a VFSNode from metadata file - flat structure"""
        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f)

        dir_path = os.path.dirname(meta_path)
        has_children = any(
            os.path.isdir(os.path.join(dir_path, entry)) and not entry.startswith('.')
            for entry in os.listdir(dir_path)
        ) if os.path.isdir(dir_path) else False

        # Flat stats - directly from meta
        return VFSNode(
            name=name,
            path=rel_path,
            node_type=meta.get('type', 'Unknown'),
            has_children=has_children,
            short_summary=meta.get('short_summary'),
            # Flat stats
            row_count=meta.get('row_count'),
            column_count=meta.get('column_count'),
            child_count=meta.get('child_count'),
            file_count=meta.get('file_count'),
            subdir_count=meta.get('subdir_count'),
            table_count=meta.get('table_count'),
            view_count=meta.get('view_count'),
            line_count=meta.get('line_count'),
            cardinality=meta.get('cardinality'),
            null_percentage=meta.get('null_percentage'),  # For Column nodes
            data_type=meta.get('data_type'),  # For Column nodes
            raw_meta=meta
        )

    def format_ls_output(
        self,
        nodes: Union[List[VFSNode], List[SerializedNode]]
    ) -> str:
        """
        Format nodes for ls command output.
        Strict 4-column format: [HasSub] | [Name] | [Info] | [Brief]
        Sorted by type priority (directories first, then files)
        """
        if not nodes:
            return "(empty)"

        # Check if we're dealing with serialized nodes
        if nodes and isinstance(nodes[0], SerializedNode):
            return self._format_serialized_ls_table(nodes)

        # Sort nodes by type priority (like VSCode)
        sorted_nodes = self._sort_nodes_by_type(nodes)

        # Regular VFSNode formatting - strict 4-column format
        lines = []
        # Header
        lines.append("[HasSub] | [Name]                    | [Info]               | [Brief]")
        lines.append("-" * 80)

        for node in sorted_nodes:
            # Get type config
            file_type = get_file_type_from_name(node.name, node.node_type)
            config = get_type_config(file_type)

            # [HasSub]: [+] for containers, [ ] for leaves
            has_sub = "[+]" if node.has_children else "[ ]"

            # [Name]: node name with type suffix
            name = self._build_ls_name_with_suffix(node)
            if config.name_max_len:
                name = name[:config.name_max_len]

            # [Info]: from config based on node type
            info = self._format_info_with_config(node, config)
            if config.info_max_len:
                info = info[:config.info_max_len]

            # [Brief]: from config (only from meta, no fallback)
            brief = get_brief_from_meta(node.raw_meta, config)
            if brief and config.brief_max_len:
                brief = brief[:config.brief_max_len]

            line = f"{has_sub:<8} | {name:<25} | {info:<20} | {brief}"
            lines.append(line)

        return "\n".join(lines)

    def _sort_nodes_by_type(self, nodes: List[VFSNode]) -> List[VFSNode]:
        """Sort nodes by type priority (directories first)"""
        def sort_key(node):
            file_type = get_file_type_from_name(node.name, node.node_type)
            config = get_type_config(file_type)
            # Priority first, then name
            return (config.priority, node.name.lower())

        return sorted(nodes, key=sort_key)

    def _format_info_with_config(self, node: VFSNode, config: TypeConfig) -> str:
        """Format [Info] field using config"""
        # For serialized file nodes, use metadata
        if "Serialized" in node.node_type:
            return format_info_from_meta(node.raw_meta, config)

        # For other nodes, use node attributes + meta
        meta = node.raw_meta
        info_data = {}

        # Add node attributes to meta for formatting
        if node.row_count is not None:
            info_data["row_count"] = node.row_count
        if node.column_count is not None:
            info_data["column_count"] = node.column_count
        if node.child_count is not None:
            info_data["child_count"] = node.child_count
        if node.table_count is not None:
            info_data["table_count"] = node.table_count
        if node.view_count is not None:
            info_data["view_count"] = node.view_count
        if node.line_count is not None:
            info_data["line_count"] = node.line_count
        if node.cardinality is not None:
            info_data["cardinality"] = node.cardinality
        if node.null_percentage is not None:
            info_data["null_percentage"] = node.null_percentage

        # Merge with raw_meta
        for key, value in meta.items():
            if key not in info_data and value is not None:
                info_data[key] = value

        return format_info_from_meta(info_data, config)

    def _format_serialized_ls_table(self, nodes: List[SerializedNode]) -> str:
        """Format serialized nodes with strict 4-column format"""
        # Sort by type (containers first), then by numeric index if possible
        def sort_key(n):
            # Try to parse name as number for LIST items
            try:
                num = int(n.name)
                return (not n.has_children, n.node_type.value, num)
            except ValueError:
                return (not n.has_children, n.node_type.value, n.name.lower())

        sorted_nodes = sorted(nodes, key=sort_key)

        lines = []
        # Header
        lines.append("[HasSub] | [Name]                    | [Info]               | [Brief]")
        lines.append("-" * 80)

        for node in sorted_nodes:
            # Get config for this node type
            config = get_type_config(node.node_type.value)

            # [HasSub]
            has_sub = "[+]" if node.has_children else "[ ]"

            # [Name]: display_name already has .TYPE suffix
            name = node.display_name
            if config.name_max_len:
                name = name[:config.name_max_len]

            # [Info]: from get_info() using config rules
            info = node.get_info()
            if config.info_max_len:
                info = info[:config.info_max_len]

            # [Brief]: from node.brief, empty if None
            brief = node.brief or ""
            if brief and config.brief_max_len:
                brief = brief[:config.brief_max_len]

            line = f"{has_sub:<8} | {name:<25} | {info:<20} | {brief}"
            lines.append(line)

        return "\n".join(lines)

    def _build_ls_name_with_suffix(self, node: VFSNode) -> str:
        """Build display name with proper type suffix for ls"""
        name = node.name

        # Extract type from name if it already has suffix
        if '.' in name:
            # Name already has suffix like users.table, id.INT.col
            return name

        # Add type suffix based on node_type
        node_type = node.node_type

        if node_type == "Database":
            return f"{name}.db"
        elif node_type == "Table":
            return f"{name}.table"
        elif node_type == "Column" and node.data_type:
            return f"{name}.{node.data_type}.col"
        elif node_type == "View":
            return f"{name}.view"
        elif node_type == "Chunk":
            return f"{name}.chunk"
        elif node_type == "FK":
            return f"{name}.fk"
        elif node_type == "Rel":
            return f"{name}.rel"
        elif node_type == "Flow":
            return f"{name}.flow"
        elif node_type == "Directory":
            return f"{name}/"
        elif node_type == "File":
            return name
        elif "Serialized" in node_type:
            # Extract type from Serialized (TYPE)
            import re
            match = re.search(r'\((\w+)\)', node_type)
            if match:
                return f"{name}.json"
            return f"{name}.json"

        return name

    def _format_info(self, node: VFSNode) -> str:
        """Format [Info] field based on node type"""
        node_type = node.node_type

        # Database: table/view count
        if node_type == "Database":
            tables = node.table_count or 0
            views = node.view_count or 0
            return f"{tables} tables, {views} views"

        # Table: row and column count
        if node_type == "Table":
            rows = node.row_count or 0
            cols = node.column_count or 0
            return f"{rows} rows, {cols} cols"

        # Column: cardinality/distinct count
        if node_type == "Column":
            if node.cardinality is not None:
                return f"Distinct: {node.cardinality}"
            if node.null_percentage is not None:
                return f"null: {node.null_percentage:.1f}%"
            return "-"

        # Chunk: line or token count
        if node_type == "Chunk":
            if node.line_count is not None:
                return f"{node.line_count} lines"
            return "-"

        # View: source count
        if node_type == "View":
            return "view"

        # Document (Markdown, TXT, PDF): line count
        if node_type in ("Document", "Markdown", "Text"):
            if node.line_count is not None:
                return f"{node.line_count} lines"
            return "-"

        # Directory: child count
        if node_type == "Directory":
            if node.child_count is not None:
                return f"{node.child_count} children"
            return "-"

        # File: no info for regular files
        if node_type == "File":
            return "-"

        # Serialized file
        if "Serialized" in node_type:
            return self._format_stats(node)

        return "-"

    def _build_ls_name(self, node: VFSNode) -> str:
        """Build display name with type annotations for ls."""
        name = node.name
        node_type = node.node_type

        # For Column: show name (data_type)
        if node_type == "Column" and node.data_type:
            return f"{name} ({node.data_type})"

        # For View: show [View] name
        if node_type == "View":
            return f"[View] {name}"

        # For Table: show name (no annotation needed for physical tables)
        if node_type == "Table":
            return name

        # For directories with children: add trailing slash
        if node.has_children:
            return f"{name}/"

        return name

    def format_meta_output(self, node: VFSNode) -> str:
        """Format node for stat command output - flat structure."""
        lines = []

        # Build display name with type info
        display_name = self._build_stat_name(node)
        lines.append(f"Name: {display_name}")
        lines.append(f"Type: {node.node_type}")
        lines.append(f"Path: {node.path}")

        if node.short_summary:
            lines.append(f"\nShort Summary: {node.short_summary}")

        long_summary = node.raw_meta.get('long_summary')
        if long_summary:
            lines.append(f"Long Summary: {long_summary}")

        # Flat stats display
        stats_items = []
        if node.row_count is not None:
            stats_items.append(f"  row_count: {node.row_count}")
        if node.column_count is not None:
            stats_items.append(f"  column_count: {node.column_count}")
        if node.child_count is not None:
            stats_items.append(f"  child_count: {node.child_count}")
        if node.file_count is not None:
            stats_items.append(f"  file_count: {node.file_count}")
        if node.subdir_count is not None:
            stats_items.append(f"  subdir_count: {node.subdir_count}")
        if node.table_count is not None:
            stats_items.append(f"  table_count: {node.table_count}")
        if node.view_count is not None:
            stats_items.append(f"  view_count: {node.view_count}")
        if node.line_count is not None:
            stats_items.append(f"  line_count: {node.line_count}")
        if node.cardinality is not None:
            stats_items.append(f"  cardinality (Distinct): {node.cardinality}")

        # For Column: show data_type
        if node.node_type == "Column" and node.data_type:
            stats_items.append(f"  data_type: {node.data_type}")

        # Additional flat stats from raw_meta
        meta = node.raw_meta
        for key in ['min_value', 'max_value', 'mean_value', 'null_count', 'null_percentage',
                    'min_length', 'max_length', 'avg_length', 'heading_count', 'link_count']:
            if key in meta and meta[key] is not None:
                stats_items.append(f"  {key}: {meta[key]}")

        if stats_items:
            lines.append("\nStatistics:")
            lines.extend(stats_items)

        # Display compact string format
        if 'top_k' in meta and meta['top_k']:
            lines.append(f"\nTop values: {meta['top_k']}")

        if 'samples' in meta and meta['samples']:
            lines.append(f"\nSamples: {meta['samples']}")

        if 'top_level_keys' in meta and meta['top_level_keys']:
            lines.append(f"\nTop-level keys: {', '.join(meta['top_level_keys'][:10])}")

        # Display joins for tables
        if 'joins' in meta and meta['joins']:
            joins = meta['joins']
            if joins:
                lines.append(f"\nJoin Relationships ({len(joins)} found):")
                for j in joins[:10]:  # Limit to 10
                    target = j.get('target_table', 'unknown')
                    src_col = j.get('source_column', 'unknown')
                    tgt_col = j.get('target_column', 'unknown')
                    conf = j.get('confidence', 0)
                    comment = j.get('comment', '')
                    lines.append(f"  {src_col} -> {target}.{tgt_col} (confidence: {conf})")
                    if comment:
                        lines.append(f"    Note: {comment}")

        return "\n".join(lines)

    def format_meta_compact(self, node: VFSNode) -> str:
        """Format node metadata in compact, token-efficient format for LLM."""
        meta = node.raw_meta
        lines = []

        # Header: Type Name
        type_name = node.node_type
        name = node.name
        if node.data_type:  # For Column
            name = f"{name}({node.data_type})"
        lines.append(f"{type_name}: {name}")

        # Core stats (single line)
        stats = []
        if node.row_count is not None:
            stats.append(f"rows={node.row_count}")
        if node.column_count is not None:
            stats.append(f"cols={node.column_count}")
        if node.cardinality is not None:
            stats.append(f"distinct={node.cardinality}")
        if node.null_percentage is not None:
            stats.append(f"null={node.null_percentage:.1f}%")
        if stats:
            lines.append(f"Stats: {', '.join(stats)}")

        # Joins (compact)
        joins = meta.get('joins')
        if joins:
            join_strs = []
            for j in joins[:5]:  # Limit to 5
                src = j.get('source_column', '?')
                tgt = f"{j.get('target_table', '?')}.{j.get('target_column', '?')}"
                conf = j.get('confidence', 0)
                join_strs.append(f"{src}->{tgt}(c{conf})")
            lines.append(f"Joins: {', '.join(join_strs)}")

        # Top K values (compact from list)
        top_k = meta.get('top_k')
        if top_k and isinstance(top_k, list):
            topk_strs = []
            for item in top_k[:5]:
                val = item.get('value', '?')
                cnt = item.get('count', 0)
                topk_strs.append(f"{val}:{cnt}")
            lines.append(f"TopK: {', '.join(topk_strs)}")

        # Samples (compact from list)
        samples = meta.get('samples')
        if samples and isinstance(samples, list):
            lines.append(f"Samples: {', '.join(str(s) for s in samples[:5])}")

        # Summary (if exists)
        short = meta.get('short_summary')
        if short:
            lines.append(f"Desc: {short}")

        return " | ".join(lines)

    def format_joins_compact(self, joins: list) -> str:
        """Format joins list in compact format."""
        if not joins:
            return "Joins: N/A"
        parts = []
        for j in joins[:5]:
            src = j.get('source_column', '?')
            tgt = f"{j.get('target_table', '?')}.{j.get('target_column', '?')}"
            conf = j.get('confidence', 0)
            parts.append(f"{src}->{tgt}(c{conf})")
        return f"Joins: {', '.join(parts)}"

    def _build_stat_name(self, node: VFSNode) -> str:
        """Build display name with type annotations for stat."""
        name = node.name
        node_type = node.node_type

        # For Column: show name (data_type)
        if node_type == "Column" and node.data_type:
            return f"{name} ({node.data_type})"

        # For View: show [View] name
        if node_type == "View":
            return f"[View] {name}"

        return name

    def _format_stats(self, node: VFSNode) -> str:
        """Format statistics for display - flat structure."""
        # Directory: child_count, file_count, subdir_count
        if node.child_count is not None:
            return f"{node.child_count} children"
        # Table/CSV: row_count, column_count
        if node.row_count is not None and node.column_count is not None:
            return f"{node.row_count} rows, {node.column_count} cols"
        if node.row_count is not None:
            return f"{node.row_count} rows"
        # DB: table_count, view_count
        if node.table_count is not None:
            return f"{node.table_count} tables, {node.view_count} views"
        # MD: line_count
        if node.line_count is not None:
            return f"{node.line_count} lines"
        # Column: cardinality
        if node.cardinality is not None:
            return f"Distinct: {node.cardinality}"

        return ""

    # ============ Serialized File (JSON/YAML) Support ============

    def _is_serialized_virtual_path(self, path: str) -> bool:
        """
        Check if path is inside a serialized file virtual directory.

        Path patterns that match:
        - data.json/ROOT.DICT/users
        - config.yaml/ROOT.DICT/database/host
        """
        if not path:
            return False

        parts = path.replace('\\', '/').split('/')
        for i, part in enumerate(parts):
            # Check if any part looks like a serialized file
            if '.' in part:
                ext = os.path.splitext(part)[1].lower()
                if ext in ('.json', '.yaml', '.yml'):
                    return True
        return False

    def _resolve_serialized_file_path(self, virtual_path: str) -> tuple:
        """
        Resolve virtual path to (file_path, internal_path, file_type).

        Args:
            virtual_path: e.g., "data.json/ROOT.DICT/users.LIST"

        Returns:
            Tuple of (absolute _raw file path, internal virtual path, file_type)
        """
        parts = virtual_path.replace('\\', '/').split('/')

        file_path_parts = []
        internal_path_parts = []
        found_file = False
        file_type = None

        for part in parts:
            if not found_file and '.' in part:
                ext = os.path.splitext(part)[1].lower()
                if ext in ('.json', '.yaml', '.yml'):
                    file_path_parts.append(part)
                    file_type = ext
                    found_file = True
                    continue

            if found_file:
                internal_path_parts.append(part)
            else:
                file_path_parts.append(part)

        # In Pontis architecture, serialized files are directories with _raw file
        file_dir = os.path.join(self.pontis_root, *file_path_parts)
        raw_file_path = os.path.join(file_dir, self.RAW_FILENAME)

        # If _raw doesn't exist, try as regular file
        if os.path.exists(raw_file_path):
            file_path = raw_file_path
        else:
            file_path = file_dir

        internal_path = '/'.join(internal_path_parts)

        return file_path, internal_path, file_type

    def _get_serialized_handler(self, file_path: str, file_type: str = None) -> SerializedVFSEngine:
        """Get or create cached handler for a serialized file"""
        # Simple caching - could be enhanced with LRU cache
        if not hasattr(self, '_serialized_cache'):
            self._serialized_cache = {}

        cache_key = f"{file_path}:{file_type}"
        if cache_key not in self._serialized_cache:
            self._serialized_cache[cache_key] = SerializedVFSEngine(file_path, file_type)

        return self._serialized_cache[cache_key]

    def _dir_has_visible_children(self, dir_path: str) -> bool:
        """检查目录是否有可见内容（除 _meta 和 _raw 外）"""
        try:
            for entry in os.listdir(dir_path):
                if entry.startswith('.'):
                    continue
                if entry == self.META_FILENAME or entry == self.RAW_FILENAME:
                    continue
                return True
            return False
        except:
            return False

    def _create_directory_node(self, name: str, rel_path: str, dir_path: str) -> VFSNode:
        """Create a VFSNode for a regular directory without metadata"""
        # Count children
        try:
            children = [e for e in os.listdir(dir_path) if not e.startswith('.')]
            child_count = len(children)
        except:
            child_count = 0

        return VFSNode(
            name=name,
            path=rel_path,
            node_type="Directory",
            has_children=child_count > 0,
            short_summary=None,
            child_count=child_count
        )

    def _create_file_node(self, name: str, rel_path: str, file_path: str) -> VFSNode:
        """Create a VFSNode for a regular file"""
        # Get file size
        try:
            size = os.path.getsize(file_path)
            size_str = self._format_size(size)
        except:
            size_str = "-"

        return VFSNode(
            name=name,
            path=rel_path,
            node_type="File",
            has_children=False,
            short_summary=None,
            child_count=0
        )

    def _format_size(self, size: int) -> str:
        """Format file size in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _create_serialized_node(self, name: str, rel_path: str, file_path: str) -> VFSNode:
        """Create a VFSNode for a serialized file (JSON, YAML, etc.)"""
        handler = self._get_serialized_handler(file_path)
        root_type = handler.root_type

        # Determine info based on root type
        data = handler._load()
        if root_type.value == "DICT":
            info = f"{len(data)} pairs" if isinstance(data, dict) else "dict"
        elif root_type.value == "LIST":
            info = f"{len(data)} items" if isinstance(data, list) else "list"
        else:
            info = f"scalar ({root_type.value})"

        return VFSNode(
            name=name,
            path=rel_path,
            node_type=f"Serialized ({root_type.value})",
            has_children=root_type.is_container(),
            short_summary=f"JSON/YAML file with {info}",
            child_count=len(data) if isinstance(data, (dict, list)) else 0
        )

    def _get_serialized_node(self, virtual_path: str) -> Optional[SerializedNode]:
        """Get a node from a serialized file by virtual path"""
        file_path, internal_path, file_type = self._resolve_serialized_file_path(virtual_path)

        if not os.path.exists(file_path):
            return None

        handler = self._get_serialized_handler(file_path, file_type)
        return handler.resolve_path(internal_path)

    def _list_serialized_path(
        self,
        virtual_path: str,
        offset: int = 0,
        limit: int = 100
    ) -> List[SerializedNode]:
        """
        List contents of a serialized file virtual directory.

        Args:
            virtual_path: Path like "data.json/ROOT.DICT" or "data.json"
            offset: Pagination offset
            limit: Pagination limit

        Returns:
            List of SerializedNode children (for internal paths) or [ROOT] node (for root)
        """
        file_path, internal_path, file_type = self._resolve_serialized_file_path(virtual_path)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Serialized file not found: {file_path}")

        handler = self._get_serialized_handler(file_path, file_type)

        # If at root (no internal_path), return ROOT node as the only child
        if not internal_path:
            return [handler.get_root_node()]

        return handler.list_directory(internal_path, offset=offset, limit=limit)
