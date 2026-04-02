"""Base classes for two-phase extraction architecture

Phase 1: Single-node extraction (extractors/)
- Each extractor handles ONE node type
- No recursion, no cross-node access
- Only extracts "self" metadata

Phase 2: Cross-node enrichment (enrichers/)
- Full tree access via NodeTree API
- Can read any node, modify any node
- For joins, AI summaries, etc.
"""
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from pathlib import Path

import yaml

from common.config import Config
from common.schemas.base import BaseNode

logger = logging.getLogger(__name__)


class NodeTree:
    """
    Read-only view of the extracted metadata tree.
    Provided to enrichers for cross-node analysis.
    """

    def __init__(self, pontis_root: str):
        self.root = pontis_root
        self._cache: Dict[str, BaseNode] = {}

    def get_node(self, rel_path: str) -> Optional[BaseNode]:
        """Load a node by relative path (e.g., 'db.sqlite/users/user_id')"""
        if rel_path in self._cache:
            return self._cache[rel_path]

        meta_path = os.path.join(self.root, rel_path, "_meta.yml")
        if not os.path.exists(meta_path):
            return None

        try:
            with open(meta_path, 'r') as f:
                data = yaml.safe_load(f)
            node = self._create_node_from_data(data)
            self._cache[rel_path] = node
            return node
        except Exception as e:
            logger.warning(f"Failed to load node {rel_path}: {e}")
            return None

    def list_children(self, rel_path: str = "") -> List[str]:
        """List child node names at a path"""
        full_path = os.path.join(self.root, rel_path)
        if not os.path.isdir(full_path):
            return []

        children = []
        for name in os.listdir(full_path):
            if name.startswith('_'):
                continue
            child_path = os.path.join(full_path, name)
            if os.path.isdir(child_path):
                children.append(name)
        return children

    def walk(self, rel_path: str = ""):
        """
        Walk the tree yielding (rel_path, node) tuples.
        Usage: for path, node in tree.walk(): ...
        """
        for root, dirs, files in os.walk(os.path.join(self.root, rel_path)):
            # Skip pontis internal dirs
            dirs[:] = [d for d in dirs if not d.startswith('_')]

            for d in dirs:
                full_path = os.path.join(root, d)
                rel = os.path.relpath(full_path, self.root)
                node = self.get_node(rel)
                if node:
                    yield rel, node

    def find_by_type(self, node_type: str) -> List[tuple]:
        """Find all nodes of a specific type"""
        results = []
        for rel_path, node in self.walk():
            if node.type.value == node_type:
                results.append((rel_path, node))
        return results

    def save_node(self, rel_path: str, node: BaseNode) -> str:
        """
        Save modified node back to _meta.yml.
        Enrichers use this to persist changes.
        """
        pontis_path = os.path.join(self.root, rel_path)
        os.makedirs(pontis_path, exist_ok=True)
        meta_path = os.path.join(pontis_path, "_meta.yml")
        with open(meta_path, 'w', encoding='utf-8') as f:
            yaml.dump(node.model_dump(exclude_none=True, mode='json'), f,
                     default_flow_style=False, allow_unicode=True, sort_keys=False)
        # Update cache
        self._cache[rel_path] = node
        return meta_path

    def _create_node_from_data(self, data: dict) -> BaseNode:
        """Factory method to create appropriate node type from YAML data"""
        from common.schemas.base import NodeType
        from common.schemas.directory import DirectoryNode
        from common.schemas.db import DBNode
        from common.schemas.table import TableNode, ViewNode
        from common.schemas.column import ColumnNode
        from common.schemas.csv import CSVNode
        from common.schemas.json import JsonNode
        from common.schemas.markdown import MarkdownNode

        type_map = {
            NodeType.DIRECTORY: DirectoryNode,
            NodeType.DB: DBNode,
            NodeType.TABLE: TableNode,
            NodeType.VIEW: ViewNode,
            NodeType.COLUMN: ColumnNode,
            NodeType.CSV: CSVNode,
            NodeType.JSON: JsonNode,
            NodeType.MARKDOWN: MarkdownNode,
        }

        node_type = data.get('type')
        node_class = type_map.get(node_type, BaseNode)
        return node_class(**data)


class BaseExtractor(ABC):
    """
    Phase 1: Single-node extractor.
    Rules:
    1. Only extract metadata for THIS node
    2. Do NOT recurse into children (engine handles that)
    3. Do NOT access parent or sibling nodes
    4. Return the node, let engine handle saving

    For containers (like DB), implement expand() to generate child nodes.
    """

    def __init__(self, config: Config):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def handles_types(self) -> List[str]:
        """List of node types this extractor can create (e.g., ['DB', 'Table'])"""
        pass

    @abstractmethod
    def can_extract(self, path: str) -> bool:
        """Check if this extractor can handle the given physical path"""
        pass

    @abstractmethod
    def extract(self, path: str, parent_rel_path: str = "") -> Optional[BaseNode]:
        """
        Extract metadata from path, return a node.

        Args:
            path: Physical file/directory path
            parent_rel_path: Relative path of parent in pontis tree (for context)

        Returns:
            The extracted node, or None if extraction failed
        """
        pass

    def expand(self, node: BaseNode, physical_path: str, pontis_path: str) -> List[BaseNode]:
        """
        Optional: Expand a container node into child nodes.
        Called by engine after extract() for container types (DB, Table).

        Args:
            node: The parent node that was just extracted
            physical_path: Physical path to the source
            pontis_path: Path in .pontis directory where children should be saved

        Returns:
            List of child nodes (not yet saved)
        """
        # Default: no children
        return []

    def save_node(self, node: BaseNode, pontis_path: str) -> str:
        """Save node to _meta.yml"""
        os.makedirs(pontis_path, exist_ok=True)
        meta_path = os.path.join(pontis_path, "_meta.yml")
        with open(meta_path, 'w', encoding='utf-8') as f:
            yaml.dump(node.model_dump(exclude_none=True, mode='json'), f,
                     default_flow_style=False, allow_unicode=True, sort_keys=False)
        return meta_path


class BaseEnricher(ABC):
    """
    Phase 2: Cross-node enricher.
    Rules:
    1. Can read ANY node in the tree
    2. Can modify ANY node (call tree.save_node_after_enrich())
    3. Run in priority order
    4. No side effects outside pontis directory
    """

    def __init__(self, config: Config):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def priority(self) -> int:
        """Execution order: lower = earlier (100=early, 500=mid, 900=late)"""
        pass

    @abstractmethod
    def should_run(self, tree: NodeTree) -> bool:
        """Check if this enricher should run (e.g., config enabled)"""
        pass

    @abstractmethod
    def enrich(self, tree: NodeTree) -> None:
        """
        Enrich the entire tree.
        Use tree.get_node(), tree.find_by_type() to read.
        Use tree.save_node() or modify in place to write.
        """
        pass
