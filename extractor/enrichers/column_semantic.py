"""Column Semantic Enricher - Phase 2

Generates AI-powered semantic descriptions for database columns.
Provides brief (format + meaning) and detail (data range, samples) fields.
"""
import json
import logging
from typing import Dict, List, Optional

from extractor.base import BaseEnricher, NodeTree
from extractor.llm import get_llm_client
from common.config import Config
from common.schemas.table import TableNode
from common.schemas.column import ColumnNode

logger = logging.getLogger(__name__)


class ColumnSemanticEnricher(BaseEnricher):
    """
    Generates semantic descriptions for columns using LLM.

    For each column, generates:
    - brief: Field format and core meaning (e.g., "CDS = County-District-School code")
    - detail: Data range, cardinality, sample values for SQL literal selection

    Priority: 800 (runs after join detection which may inform column semantics)
    """

    def __init__(self, config: Config):
        super().__init__(config)
        self.llm = get_llm_client(config)

    @property
    def priority(self) -> int:
        return 800

    def should_run(self, tree: NodeTree) -> bool:
        """Run if LLM is enabled"""
        return self.config.llm_enabled

    def enrich(self, tree: NodeTree) -> None:
        """Generate semantic descriptions for all columns"""
        logger.info("Enriching column semantics...")

        # Find all tables and their columns
        tables = []
        for rel_path, node in tree.walk():
            if isinstance(node, TableNode):
                tables.append((rel_path, node))

        for table_path, table_node in tables:
            self._enrich_table_columns(tree, table_path, table_node)

    def _enrich_table_columns(
        self, tree: NodeTree, table_path: str, table_node: TableNode
    ) -> None:
        """Enrich all columns for a single table"""
        table_name = table_node.name

        # Get all columns for this table
        columns = self._get_table_columns(tree, table_path)
        if not columns:
            return

        # Get all column names for context
        all_column_names = [col_name for col_name, _ in columns]

        for col_name, column_node in columns:
            try:
                brief, detail = self._generate_column_description(
                    table_name=table_name,
                    column_name=col_name,
                    column=column_node,
                    all_column_names=all_column_names
                )

                if brief:
                    column_node.brief = brief
                if detail:
                    column_node.detail = detail

                # Save updated column
                col_path = f"{table_path}/{col_name}"
                tree.save_node(col_path, column_node)

                logger.debug(f"Enriched column {table_name}.{col_name}")

            except Exception as e:
                logger.warning(f"Failed to enrich column {table_name}.{col_name}: {e}")

    def _get_table_columns(
        self, tree: NodeTree, table_path: str
    ) -> List[tuple]:
        """Get all columns for a table"""
        columns = []
        for child_name in tree.list_children(table_path):
            child_path = f"{table_path}/{child_name}"
            node = tree.get_node(child_path)
            if isinstance(node, ColumnNode):
                columns.append((child_name, node))
        return columns

    def _generate_column_description(
        self,
        table_name: str,
        column_name: str,
        column: ColumnNode,
        all_column_names: List[str]
    ) -> tuple:
        """
        Generate brief and detail for a column using LLM.
        Retries if brief exceeds max_words limit.

        Returns: (brief, detail)
        """
        max_words = getattr(self.config, 'brief_max_words', 20)
        max_retries = 2

        for attempt in range(max_retries):
            prompt = self._build_column_prompt(
                table_name, column_name, column, all_column_names,
                max_words=max_words
            )

            response = self.llm.complete(prompt, max_tokens=800)
            brief, detail = self._parse_response(response)

            if brief:
                word_count = len(brief.split())
                if word_count <= max_words:
                    return brief, detail
                elif attempt < max_retries - 1:
                    logger.debug(f"Brief too long ({word_count} words), retrying...")
                    continue
                else:
                    # Truncate on final attempt
                    brief = self._truncate_to_words(brief, max_words)

        return brief, detail

    def _parse_response(self, response: str) -> tuple:
        """Parse LLM JSON response, handling markdown code blocks."""
        try:
            json_str = response.strip()
            if json_str.startswith("```json"):
                json_str = json_str[7:]
            elif json_str.startswith("```"):
                json_str = json_str[3:]
            if json_str.endswith("```"):
                json_str = json_str[:-3]
            json_str = json_str.strip()

            result = json.loads(json_str)
            brief = result.get("brief", "").strip()
            detail = result.get("detail", "").strip()
            return brief, detail
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM response as JSON: {response[:200]}")
            return None, None

    def _truncate_to_words(self, text: str, max_words: int) -> str:
        """Truncate text to max_words, adding '...' if truncated."""
        words = text.split()
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words]) + "..."

    def _build_column_prompt(
        self,
        table_name: str,
        column_name: str,
        column: ColumnNode,
        all_column_names: List[str],
        max_words: int = 20
    ) -> str:
        """Build prompt for column semantic analysis"""

        # Build column stats info
        stats_parts = []
        if column.data_type:
            stats_parts.append(f"Data Type: {column.data_type}")
        if column.nullable is not None:
            stats_parts.append(f"Nullable: {column.nullable}")
        if column.cardinality is not None:
            stats_parts.append(f"Unique Values: {column.cardinality}")
        if column.null_count is not None:
            stats_parts.append(f"Null Count: {column.null_count}")
        if column.min_value is not None:
            stats_parts.append(f"Min: {column.min_value}")
        if column.max_value is not None:
            stats_parts.append(f"Max: {column.max_value}")
        if column.mean_value is not None:
            stats_parts.append(f"Mean: {column.mean_value:.2f}")
        if column.min_length is not None:
            stats_parts.append(f"Min Length: {column.min_length}")
        if column.max_length is not None:
            stats_parts.append(f"Max Length: {column.max_length}")

        # Top K values
        top_k_str = ""
        if column.top_k:
            top_k_items = [f"{item.get('value')} ({item.get('count')})" for item in column.top_k[:5]]
            top_k_str = "\nMost Frequent Values:\n" + "\n".join(f"  - {item}" for item in top_k_items)

        # Sample values
        samples_str = ""
        if column.samples:
            samples_str = "\nSample Values:\n" + "\n".join(f"  - {s}" for s in column.samples[:5])

        stats_text = "\n".join(stats_parts) + top_k_str + samples_str

        prompt = f"""You are a data analyst examining a database column. Provide concise semantic descriptions.

Table Name: {table_name}
Column Name: {column_name}

Other Columns in This Table (for context):
{chr(10).join(f"  - {name}" for name in all_column_names)}

Column Statistics:
{stats_text}

Your task:
1. **brief**: MAXIMUM {max_words} WORDS. Extremely concise description:
   - What this column represents (decode abbreviations like CDS=County-District-School)
   - Format/pattern only if non-obvious

   GOOD examples (short):
   - "Unique user identifier, foreign key to users table"
   - "CDS code: County-District-School identifier"
   - "ISO 8601 timestamp of event occurrence"

   BAD examples (too verbose):
   - "The user_id column represents a unique identifier... allowing queries to filter..."

2. **detail**: Detailed information for SQL query generation:
   - Data range (min/max values or length)
   - Cardinality (how many unique values)
   - 3-5 representative sample values for SQL literals
   - Patterns/formats for WHERE clauses

Respond in JSON format:
{{"brief": "...", "detail": "..."}}
"""
        return prompt
