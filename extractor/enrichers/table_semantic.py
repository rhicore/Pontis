"""Table Semantic Enricher - Phase 2

Generates AI-powered semantic summary for database tables.
Uses column metadata (including brief/detail from ColumnSemanticEnricher) and join relationships.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple

from extractor.base import BaseEnricher, NodeTree
from extractor.llm import get_llm_client
from common.config import Config
from common.schemas.table import TableNode
from common.schemas.column import ColumnNode

logger = logging.getLogger(__name__)


class TableSemanticEnricher(BaseEnricher):
    """
    Generates semantic descriptions for tables using LLM.

    For each table, generates:
    - brief: Short summary for ls display (max 20 words)
    - detail: Detailed description for SQL generation guidance

    Considers:
    - All column metadata (including brief/detail from ColumnSemanticEnricher)
    - Join relationships (foreign keys and reverse references)
    - Table statistics

    Priority: 900 (runs after ColumnSemanticEnricher)
    """

    def __init__(self, config: Config):
        super().__init__(config)
        self.llm = get_llm_client(config)

    @property
    def priority(self) -> int:
        return 900

    def should_run(self, tree: NodeTree) -> bool:
        """Run if LLM is enabled"""
        return self.config.llm_enabled

    def enrich(self, tree: NodeTree) -> None:
        """Generate semantic descriptions for all tables"""
        logger.info("Enriching table semantics...")

        # Find all tables
        tables = []
        for rel_path, node in tree.walk():
            if isinstance(node, TableNode):
                tables.append((rel_path, node))

        for table_path, table_node in tables:
            try:
                brief, detail = self._generate_table_description(
                    tree, table_path, table_node
                )

                if brief:
                    table_node.brief = brief
                if detail:
                    table_node.detail = detail

                tree.save_node(table_path, table_node)
                logger.info(f"Generated summary for table {table_node.name}")
            except Exception as e:
                logger.warning(f"Failed to enrich table {table_node.name}: {e}")

    def _generate_table_description(
        self, tree: NodeTree, table_path: str, table_node: TableNode
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Generate brief and detail for a table using LLM.
        Retries if brief exceeds max_words limit.

        Returns: (brief, detail)
        """
        max_words = getattr(self.config, 'brief_max_words', 20)
        max_retries = 2

        for attempt in range(max_retries):
            prompt = self._build_table_prompt(
                tree, table_path, table_node, max_words=max_words
            )

            response = self.llm.complete(prompt, max_tokens=1000)
            brief, detail = self._parse_response(response)

            if brief:
                word_count = len(brief.split())
                if word_count <= max_words:
                    return brief, detail
                elif attempt < max_retries - 1:
                    logger.debug(f"Table brief too long ({word_count} words), retrying...")
                    continue
                else:
                    # Truncate on final attempt
                    brief = self._truncate_to_words(brief, max_words)

        return brief, detail

    def _parse_response(self, response: str) -> Tuple[Optional[str], Optional[str]]:
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

    def _build_table_prompt(
        self, tree: NodeTree, table_path: str, table_node: TableNode, max_words: int = 20
    ) -> str:
        """Build prompt for table semantic analysis"""
        table_name = table_node.name

        # Get table stats
        stats_parts = []
        if table_node.row_count is not None:
            stats_parts.append(f"Row Count: {table_node.row_count}")
        stats_parts.append(f"Column Count: {table_node.column_count}")
        if table_node.primary_key:
            stats_parts.append(f"Primary Key: {table_node.primary_key}")

        stats_text = "\n".join(stats_parts)

        # Get all columns with their metadata
        columns_info = []
        for child_name in tree.list_children(table_path):
            child_path = f"{table_path}/{child_name}"
            node = tree.get_node(child_path)
            if isinstance(node, ColumnNode):
                col_info = self._format_column_info(child_name, node)
                columns_info.append(col_info)

        columns_text = "\n\n".join(columns_info)

        # Get join relationships
        joins_text = ""
        if table_node.joins:
            join_parts = []
            for j in table_node.joins:
                target = j.get("target_table", "?")
                source_col = j.get("source_column", "?")
                target_col = j.get("target_column", "?")
                confidence = j.get("confidence", 0.0)
                comment = j.get("comment", "")

                if "Reverse" in comment:
                    join_parts.append(f"  - This table is referenced by '{target}' via {source_col}")
                else:
                    join_parts.append(f"  - {source_col} -> {target}.{target_col} (confidence: {confidence})")
            joins_text = "\n".join(join_parts)
        else:
            joins_text = "  (No join relationships detected)"

        prompt = f"""You are a data analyst examining a database table. Provide concise semantic descriptions.

Table Name: {table_name}

Table Statistics:
{stats_text}

Columns (with semantic descriptions):
{columns_text}

Join Relationships:
{joins_text}

Your task:
1. **brief**: MAXIMUM {max_words} WORDS. Extremely concise summary:
   - What this table represents (core business/semantic entity)
   - Key purpose in one short phrase

   GOOD examples (short):
   - "Stores user account information and authentication details"
   - "CDS enrollment records: County-District-School student data"
   - "Event calendar tracking meetings, games, and social activities"

   BAD examples (too verbose):
   - "This table serves as a comprehensive repository of user account information..."

2. **detail**: Comprehensive information for SQL generation:
   - What this table represents (2-3 sentences)
   - Key columns and their significance (important fields, abbreviations)
   - Relationships to other tables (FKs, reverse references, one-to-many)
   - Usage guidance (common query patterns, important filters, data quality notes)

Respond in JSON format:
{{"brief": "...", "detail": "..."}}
"""
        return prompt

    def _format_column_info(self, col_name: str, column: ColumnNode) -> str:
        """Format column information for the prompt"""
        parts = [f"Column: {col_name}"]

        if column.data_type:
            parts.append(f"  Type: {column.data_type}")

        # Include AI-generated descriptions if available
        if column.brief:
            parts.append(f"  Brief: {column.brief}")

        # Include key stats
        stats = []
        if column.cardinality is not None:
            stats.append(f"{column.cardinality} unique")
        if column.null_count is not None:
            stats.append(f"{column.null_count} nulls")
        if column.min_value is not None and column.max_value is not None:
            stats.append(f"range [{column.min_value}, {column.max_value}]")

        if stats:
            parts.append(f"  Stats: {', '.join(stats)}")

        return "\n".join(parts)
