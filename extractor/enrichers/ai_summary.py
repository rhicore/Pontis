"""AI Summary Enricher - Phase 2

Generates AI-powered descriptions for nodes.
Requires LLM configuration.
"""
import logging
from typing import Optional

from extractor.base import BaseEnricher, NodeTree
from common.config import Config
from common.schemas.base import BaseNode
from common.schemas.table import TableNode, ViewNode
from common.schemas.column import ColumnNode
from common.schemas.markdown import MarkdownNode

logger = logging.getLogger(__name__)


class AISummaryEnricher(BaseEnricher):
    """
    Generates AI summaries for extracted nodes.
    Runs in Phase 2 with full tree access.
    Priority: 500 (middle - after basic extraction, before late enrichers)
    """

    def __init__(self, config: Config):
        super().__init__(config)
        self.llm_client = None
        if config.llm_enabled:
            from extractor.llm import get_llm_client
            self.llm_client = get_llm_client(config)

    @property
    def priority(self) -> int:
        return 500

    def should_run(self, tree: NodeTree) -> bool:
        """Only run if LLM is enabled and configured"""
        return self.config.llm_enabled and self.llm_client is not None

    def enrich(self, tree: NodeTree) -> None:
        """Generate AI summaries for all eligible nodes"""
        logger.info("Generating AI summaries...")

        for rel_path, node in tree.walk():
            try:
                if self._should_enrich_node(node):
                    self._enrich_node(node, tree)
                    # Save modified node back
                    tree.save_node(rel_path, node)
            except Exception as e:
                logger.warning(f"Failed to enrich {rel_path}: {e}")

    def _should_enrich_node(self, node: BaseNode) -> bool:
        """Check if node should be enriched"""
        # Skip if already has summary
        if node.short_summary:
            return False
        # Only enrich certain types
        return isinstance(node, (TableNode, ViewNode, ColumnNode, MarkdownNode))

    def _enrich_node(self, node: BaseNode, tree: NodeTree) -> None:
        """Generate summary for a specific node"""
        if isinstance(node, (TableNode, ViewNode)):
            self._enrich_table(node)
        elif isinstance(node, ColumnNode):
            self._enrich_column(node)
        elif isinstance(node, MarkdownNode):
            self._enrich_markdown(node)

    def _enrich_table(self, node: TableNode) -> None:
        """Generate AI summary for table/view"""
        is_view = isinstance(node, ViewNode)
        entity_type = "View" if is_view else "Table"

        prompt = f"""Analyze this database {entity_type.lower()} and provide brief descriptions.

{entity_type} Name: {node.name}
Row Count: {node.row_count or 'N/A'}
Column Count: {node.column_count}
Primary Key: {node.primary_key or 'N/A'}

Provide:
Short: A brief (5-10 words) description
Long: A more detailed description

Format:
Short: <short description>
Long: <long description>"""

        try:
            result = self.llm_client.complete(prompt)
            for line in result.strip().split('\n'):
                if line.startswith('Short:'):
                    node.short_summary = line[6:].strip()
                elif line.startswith('Long:'):
                    node.long_summary = line[5:].strip()
            logger.debug(f"Enriched {entity_type}: {node.name}")
        except Exception as e:
            logger.warning(f"Failed to enrich {entity_type} {node.name}: {e}")

    def _enrich_column(self, node: ColumnNode) -> None:
        """Generate AI summary for column"""
        # Get samples from list
        sample_str = ', '.join(str(s) for s in node.samples[:3]) if node.samples else 'N/A'

        prompt = f"""Analyze this database column and provide brief descriptions.

Column Name: {node.name}
Data Type: {node.data_type}
Sample Values: {sample_str}

Provide:
Short: A brief (5-10 words) description
Long: A more detailed description

Format:
Short: <short description>
Long: <long description>"""

        try:
            result = self.llm_client.complete(prompt)
            for line in result.strip().split('\n'):
                if line.startswith('Short:'):
                    node.short_summary = line[6:].strip()
                elif line.startswith('Long:'):
                    node.long_summary = line[5:].strip()
            logger.debug(f"Enriched column: {node.name}")
        except Exception as e:
            logger.warning(f"Failed to enrich column {node.name}: {e}")

    def _enrich_markdown(self, node: MarkdownNode) -> None:
        """Generate AI summary for markdown"""
        content = node.first_paragraph or ''

        prompt = f"""Analyze this markdown document and provide brief descriptions.

Document: {node.name}
Preview: {content[:500] or 'N/A'}

Provide:
Short: A brief (5-10 words) description
Long: A more detailed description

Format:
Short: <short description>
Long: <long description>"""

        try:
            result = self.llm_client.complete(prompt)
            for line in result.strip().split('\n'):
                if line.startswith('Short:'):
                    node.short_summary = line[6:].strip()
                elif line.startswith('Long:'):
                    node.long_summary = line[5:].strip()
            logger.debug(f"Enriched markdown: {node.name}")
        except Exception as e:
            logger.warning(f"Failed to enrich markdown {node.name}: {e}")
