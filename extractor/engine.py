"""Two-phase extraction engine

Phase 1: Build the tree (single-node extraction)
Phase 2: Enrich the tree (cross-node analysis)
"""
import os
import logging
from typing import List, Optional, Type
from pathlib import Path

from extractor.config import ExtractorConfig
from common.schemas.base import BaseNode
from extractor.base import BaseExtractor, BaseEnricher, NodeTree

logger = logging.getLogger(__name__)


class ExtractorEngine:
    """
    Two-phase extraction engine:

    Phase 1 - Build:
        Walk physical directory
        For each file/dir: find extractor, create node
        Result: complete pontis tree with basic metadata

    Phase 2 - Enrich:
        Run registered enrichers in priority order
        Each enricher can access and modify entire tree
        Result: enriched metadata (joins, AI summaries, etc.)
    """

    def __init__(self, config: Optional[ExtractorConfig] = None):
        self.config = config or Config()
        self._extractors: List[BaseExtractor] = []
        self._enrichers: List[BaseEnricher] = []

    def register_extractor(self, extractor: BaseExtractor):
        """Register a single-node extractor"""
        self._extractors.append(extractor)
        logger.debug(f"Registered extractor: {extractor.__class__.__name__}")

    def register_enricher(self, enricher: BaseEnricher):
        """Register a cross-node enricher"""
        self._enrichers.append(enricher)
        # Keep sorted by priority
        self._enrichers.sort(key=lambda e: e.priority)
        logger.debug(f"Registered enricher: {enricher.__class__.__name__} (priority: {enricher.priority})")

    def extract(self, target_path: str) -> str:
        """
        Run full two-phase extraction.

        Args:
            target_path: Physical directory to scan

        Returns:
            Path to generated .pontis directory
        """
        if not os.path.isdir(target_path):
            raise ValueError(f"Not a directory: {target_path}")

        pontis_path = os.path.join(target_path, self.config.pontis_dir_name)
        os.makedirs(pontis_path, exist_ok=True)

        logger.info(f"=== Phase 1: Building tree from {target_path} ===")
        self._phase1_build(target_path, pontis_path)

        logger.info(f"=== Phase 2: Enriching tree ===")
        self._phase2_enrich(pontis_path)

        logger.info(f"Extraction complete: {pontis_path}")
        return pontis_path

    def _phase1_build(self, physical_root: str, pontis_root: str, rel_path: str = "", depth: int = 0):
        """
        Phase 1: Recursively build the tree.
        Each node is extracted independently, then expanded if it's a container.
        """
        physical_path = os.path.join(physical_root, rel_path)
        pontis_path = os.path.join(pontis_root, rel_path)

        # Find an extractor for this path
        extractor = self._find_extractor(physical_path)
        if extractor is None:
            logger.debug(f"No extractor for: {physical_path}")
            return

        try:
            # Extract this node (no recursion yet)
            node = extractor.extract(physical_path, rel_path)
            if not node:
                return

            # Save node metadata
            extractor.save_node(node, pontis_path)
            indent = "  " * depth
            logger.info(f"{indent}Extracted: {rel_path or '.'} ({node.type.value})")

            # Expand: generate child nodes (e.g., DB -> Tables, Table -> Columns)
            children = extractor.expand(node, physical_path, pontis_path)
            for child_node, child_name in children:
                child_rel = os.path.join(rel_path, child_name) if rel_path else child_name
                child_pontis = os.path.join(pontis_path, child_name)
                # Save child
                extractor.save_node(child_node, child_pontis)
                logger.info(f"{indent}  + {child_name} ({child_node.type.value})")

                # Recursively expand children (for nested containers)
                # We pass the parent's physical path since children share the same source
                if hasattr(extractor, '_physical_path_for_children'):
                    child_physical = extractor._physical_path_for_children
                else:
                    child_physical = physical_path
                # For DB children (tables), physical path stays the same
                # For table children (columns), physical path also stays the same
                self._expand_children_recursive(child_node, child_name, child_physical, pontis_path, pontis_root, depth + 2)

            # For directories, also recurse into filesystem children
            if os.path.isdir(physical_path):
                self._recurse_children(physical_root, pontis_root, rel_path, depth)

        except Exception as e:
            logger.error(f"Failed to extract {rel_path}: {e}")

    def _expand_children_recursive(self, node, node_name, physical_path, parent_pontis_path, pontis_root, depth: int):
        """Recursively expand container nodes (DB->Tables->Columns)"""
        # Find the appropriate extractor for this node type
        node_type = node.type.value

        # Get the extractor for this node type
        extractor = None
        for ext in self._extractors:
            if node_type in ext.handles_types:
                extractor = ext
                break

        if not extractor:
            return

        # Try to expand
        try:
            node_pontis_path = os.path.join(parent_pontis_path, node_name)
            children = extractor.expand(node, physical_path, node_pontis_path)

            for child_node, child_name in children:
                child_pontis = os.path.join(node_pontis_path, child_name)
                extractor.save_node(child_node, child_pontis)
                indent = "  " * depth
                logger.info(f"{indent}+ {child_name} ({child_node.type.value})")

                # Recurse deeper (e.g., columns)
                self._expand_children_recursive(child_node, child_name, physical_path, node_pontis_path, pontis_root, depth + 1)
        except Exception as e:
            logger.debug(f"Could not expand {node_name}: {e}")

    def _recurse_children(self, physical_root: str, pontis_root: str, parent_rel_path: str, depth: int):
        """Recurse into child directories/files"""
        physical_parent = os.path.join(physical_root, parent_rel_path)

        for name in os.listdir(physical_parent):
            # Skip hidden and pontis dirs
            if name.startswith('.') or name == self.config.pontis_dir_name:
                continue

            child_rel_path = os.path.join(parent_rel_path, name) if parent_rel_path else name
            self._phase1_build(physical_root, pontis_root, child_rel_path, depth)

    def _phase2_enrich(self, pontis_root: str):
        """
        Phase 2: Run all enrichers on the complete tree.
        """
        tree = NodeTree(pontis_root)

        for enricher in self._enrichers:
            try:
                if enricher.should_run(tree):
                    logger.info(f"Running enricher: {enricher.__class__.__name__}")
                    enricher.enrich(tree)
                else:
                    logger.debug(f"Skipping enricher: {enricher.__class__.__name__}")
            except Exception as e:
                logger.error(f"Enricher {enricher.__class__.__name__} failed: {e}")

    def _find_extractor(self, path: str) -> Optional[BaseExtractor]:
        """Find first extractor that can handle this path"""
        for extractor in self._extractors:
            try:
                if extractor.can_extract(path):
                    return extractor
            except Exception as e:
                logger.debug(f"Extractor check failed: {e}")
        return None


class ModularEngine(ExtractorEngine):
    """
    Pre-configured engine with standard extractors and enrichers.
    Users can extend by registering additional ones.
    """

    def __init__(self, config: Optional[ExtractorConfig] = None):
        super().__init__(config)
        self._register_default_extractors()
        self._register_default_enrichers()

    def _register_default_extractors(self):
        """Register built-in extractors"""
        from extractor.extractors.directory import DirectoryExtractor
        from extractor.extractors.db import DBExtractor
        from extractor.extractors.table import TableExtractor
        from extractor.extractors.column import ColumnExtractor
        from extractor.extractors.csv import CSVExtractor
        from extractor.extractors.json import JsonExtractor
        from extractor.extractors.markdown import MarkdownExtractor

        # Order matters: more specific first
        self.register_extractor(DBExtractor(self.config))
        self.register_extractor(CSVExtractor(self.config))
        self.register_extractor(JsonExtractor(self.config))
        self.register_extractor(MarkdownExtractor(self.config))
        self.register_extractor(DirectoryExtractor(self.config))

        # Internal extractors (for expand operations, not file detection)
        self.register_extractor(TableExtractor(self.config))
        self.register_extractor(ColumnExtractor(self.config))

    def _register_default_enrichers(self):
        """Register built-in enrichers (if enabled)"""
        # Phase 2a: Relationship detection (fast, no LLM needed)
        from extractor.enrichers.join_relation import JoinRelationEnricher
        self.register_enricher(JoinRelationEnricher(self.config))

        # Phase 2b: Semantic enrichment (requires LLM)
        if self.config.llm_enabled:
            # Column-level semantic analysis
            from extractor.enrichers.column_semantic import ColumnSemanticEnricher
            self.register_enricher(ColumnSemanticEnricher(self.config))

            # Table-level semantic summary
            from extractor.enrichers.table_semantic import TableSemanticEnricher
            self.register_enricher(TableSemanticEnricher(self.config))
