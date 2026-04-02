"""Virtual File System interface for .pontis metadata - updated for flat schema"""
import os
import yaml
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


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

    def __init__(self, pontis_root: str):
        if not os.path.exists(pontis_root):
            raise ValueError(f".pontis directory not found: {pontis_root}")
        self.pontis_root = os.path.abspath(pontis_root)

    def list_directory(self, path: str = "") -> List[VFSNode]:
        """List contents of a virtual directory."""
        full_path = os.path.join(self.pontis_root, path)

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Path not found: {path}")
        if not os.path.isdir(full_path):
            raise NotADirectoryError(f"Not a directory: {path}")

        nodes = []
        for entry in os.listdir(full_path):
            if entry.startswith('.'):
                continue
            entry_path = os.path.join(full_path, entry)
            rel_path = os.path.join(path, entry) if path else entry
            if os.path.isdir(entry_path):
                meta_path = os.path.join(entry_path, self.META_FILENAME)
                if os.path.exists(meta_path):
                    node = self._load_node(entry, rel_path, meta_path)
                    nodes.append(node)

        return sorted(nodes, key=lambda n: (not n.has_children, n.name.lower()))

    def get_node_info(self, path: str) -> VFSNode:
        """Get detailed information about a node."""
        full_path = os.path.join(self.pontis_root, path)

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

    def format_ls_output(self, nodes: List[VFSNode]) -> str:
        """Format nodes for ls command output."""
        lines = []
        for node in nodes:
            child_indicator = "D" if node.has_children else "F"
            stats_summary = self._format_stats(node)

            # Build display name with type info
            name_display = self._build_ls_name(node)

            short = node.short_summary or ""
            if len(short) > 50:
                short = short[:47] + "..."
            line = f"[{child_indicator}] {name_display:<35} {node.node_type:<12} {stats_summary:<20} {short}"
            lines.append(line)
        return "\n".join(lines)

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
