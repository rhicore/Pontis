"""Join Relationship Enricher - Phase 2

Analyzes all columns across tables to detect potential join relationships.
This is an example of cross-node enrichment that requires full tree access.
"""
import os
import logging
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict

from extractor.base import BaseEnricher, NodeTree
from common.config import Config
from common.schemas.table import TableNode
from common.schemas.column import ColumnNode

logger = logging.getLogger(__name__)


class JoinRelationEnricher(BaseEnricher):
    """
    Detects potential join relationships between tables.

    Algorithm:
    1. Find all columns ending with '_id' (potential FKs)
    2. Match against table names (e.g., 'user_id' -> 'users' table)
    3. Check data type compatibility
    4. Optionally sample value overlap (future)

    Priority: 700 (late - requires all columns to be available)
    """

    # Patterns that suggest foreign key columns
    FK_SUFFIXES = ['_id', '_key', '_code', 'id', 'uuid']

    def __init__(self, config: Config):
        super().__init__(config)
        self._column_index: Dict[str, List[Tuple[str, ColumnNode]]] = defaultdict(list)

    @property
    def priority(self) -> int:
        return 700

    def should_run(self, tree: NodeTree) -> bool:
        """Always run - this is fast and useful"""
        return True

    def enrich(self, tree: NodeTree) -> None:
        """
        Enrich join relationships:
        1. AI-detect additional joins based on column name patterns
        2. Generate reverse (inbound) join records for all tables

        Note: Explicit foreign keys are already extracted by TableExtractor (Phase 1)
        """
        logger.info("Enriching join relationships...")

        # Step 1: Build index of all columns by normalized name
        self._build_column_index(tree)

        # Step 2: AI-detect additional joins and collect all joins for reverse generation
        all_joins = []  # [(source_path, join_dict), ...]

        for rel_path, node in tree.walk():
            if not isinstance(node, TableNode):
                continue

            # Collect existing joins from Phase 1 (extractor)
            if node.joins:
                for j in node.joins:
                    all_joins.append((rel_path, j))

            # AI-detect additional joins (enricher)
            try:
                detected_joins = self._detect_joins_for_table(node, rel_path, tree)
                for j in detected_joins:
                    # Check if this join already exists (avoid duplicates)
                    is_duplicate = any(
                        existing[0] == rel_path and
                        existing[1].get("target_table") == j.get("target_table") and
                        existing[1].get("source_column") == j.get("source_column")
                        for existing in all_joins
                    )
                    if not is_duplicate:
                        all_joins.append((rel_path, j))
                        if not node.joins:
                            node.joins = []
                        node.joins.append(j)
                        logger.debug(f"AI detected join: {node.name}.{j['source_column']} -> {j['target_table']}")

                if detected_joins:
                    logger.info(f"AI detected {len(detected_joins)} new joins for {node.name}")
            except Exception as e:
                logger.warning(f"Failed to detect joins for {rel_path}: {e}")

        # Step 3: Generate reverse joins (enricher - requires all tables info)
        self._add_reverse_joins(tree, all_joins)

    def _build_column_index(self, tree: NodeTree) -> None:
        """Index all columns by their normalized names"""
        self._column_index.clear()

        for rel_path, node in tree.walk():
            if isinstance(node, ColumnNode):
                # Index by column name
                self._column_index[node.name.lower()].append((rel_path, node))

                # Index by base name (e.g., 'user_id' -> 'user')
                for suffix in self.FK_SUFFIXES:
                    if node.name.lower().endswith(suffix):
                        base = node.name.lower()[:-len(suffix)]
                        if base:
                            self._column_index[base].append((rel_path, node))

    def _detect_joins_for_table(
        self, table: TableNode, table_path: str, tree: NodeTree
    ) -> List[Dict]:
        """
        Detect potential joins for a single table using AI heuristics.
        Skips columns that already have explicit foreign keys (from Phase 1 extractor).
        """
        joins = []
        table_name = table.name.lower()

        # Get columns that already have explicit FKs (skip these)
        explicit_fk_columns = set()
        if table.joins:
            for j in table.joins:
                if j.get("confidence") == 1.0:
                    explicit_fk_columns.add(j.get("source_column"))

        # Get this table's columns
        table_columns = self._get_table_columns(table_path, tree)

        for col_name, column in table_columns:
            # Skip if this column already has an explicit FK
            if col_name in explicit_fk_columns:
                continue

            col_lower = col_name.lower()

            # Pattern: Column looks like FK (ends with _id)
            for suffix in self.FK_SUFFIXES:
                if col_lower.endswith(suffix):
                    base = col_lower[:-len(suffix)]
                    target_table = self._pluralize(base)

                    # Find target table
                    target_path = self._find_table_path(target_table, tree)
                    if target_path and target_path != table_path:
                        # Check if target has matching column
                        target_pk = self._find_primary_key(target_path, tree)

                        if target_pk:
                            confidence = 0.8 if col_lower == f"{base}_id" else 0.6
                            joins.append({
                                "target_table": os.path.basename(target_path),
                                "target_column": target_pk,
                                "source_column": col_name,
                                "confidence": confidence,
                                "comment": f"AI detected: column name pattern '{col_name}' suggests join to '{target_table}'"
                            })

        # Deduplicate
        seen = set()
        unique_joins = []
        for j in joins:
            key = (j["source_column"], j["target_table"])
            if key not in seen:
                seen.add(key)
                unique_joins.append(j)

        return unique_joins

    def _get_table_columns(self, table_path: str, tree: NodeTree) -> List[Tuple[str, ColumnNode]]:
        """Get all columns for a table"""
        columns = []
        for child_name in tree.list_children(table_path):
            child_path = f"{table_path}/{child_name}" if table_path else child_name
            node = tree.get_node(child_path)
            if isinstance(node, ColumnNode):
                columns.append((child_name, node))
        return columns

    def _find_table_path(self, table_name: str, tree: NodeTree) -> Optional[str]:
        """Find path to table by name (case-insensitive)"""
        table_lower = table_name.lower()

        for rel_path, node in tree.walk():
            if isinstance(node, TableNode):
                if node.name.lower() == table_lower:
                    return rel_path
                # Also try singular/plural variants
                if self._pluralize(node.name.lower()) == table_lower:
                    return rel_path
                if self._singularize(node.name.lower()) == table_lower:
                    return rel_path

        return None

    def _find_primary_key(self, table_path: str, tree: NodeTree) -> Optional[str]:
        """Find primary key column for a table"""
        table_node = tree.get_node(table_path)
        if isinstance(table_node, TableNode) and table_node.primary_key:
            return table_node.primary_key

        # Default to 'id' column if exists
        for child_name in tree.list_children(table_path):
            if child_name.lower() == 'id':
                return child_name

        return None

    def _add_reverse_joins(self, tree: NodeTree, all_joins: List[Tuple[str, Dict]]) -> None:
        """Add reverse (inbound) join records to target tables"""
        for source_path, join in all_joins:
            target_table_name = join.get("target_table")
            if not target_table_name:
                continue

            # Find target table node
            target_path = self._find_table_path(target_table_name.lower(), tree)
            if not target_path:
                continue

            target_node = tree.get_node(target_path)
            if not isinstance(target_node, TableNode):
                continue

            # Create reverse join record
            reverse_join = {
                "target_table": os.path.basename(source_path),  # Source becomes target
                "target_column": join.get("source_column"),  # Source column becomes target
                "source_column": join.get("target_column"),  # Target column becomes source
                "confidence": join.get("confidence"),
                "comment": f"Reverse: this table is referenced by '{os.path.basename(source_path)}'"
            }

            # Add to target table's joins
            if not target_node.joins:
                target_node.joins = []
            target_node.joins.append(reverse_join)

            # Save updated target node
            tree.save_node(target_path, target_node)
            logger.info(f"Added reverse join to {target_table_name} from {os.path.basename(source_path)}")

    def _pluralize(self, word: str) -> str:
        """Simple pluralization"""
        if not word:
            return word
        if word.endswith('s') or word.endswith('es'):
            return word
        if word.endswith('y'):
            return word[:-1] + 'ies'
        return word + 's'

    def _singularize(self, word: str) -> str:
        """Simple singularization"""
        if not word:
            return word
        if word.endswith('ies'):
            return word[:-3] + 'y'
        if word.endswith('es'):
            return word[:-2]
        if word.endswith('s') and not word.endswith('ss'):
            return word[:-1]
        return word
