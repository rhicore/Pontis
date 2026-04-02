"""Cross-node enrichers - Phase 2 of extraction

Each enricher has full tree access for cross-node analysis.
Run in priority order after all nodes are extracted.

To add a new enricher:
1. Create a file in this directory
2. Inherit from BaseEnricher
3. Implement priority, should_run(), enrich()
4. Register in ModularEngine
"""
from extractor.enrichers.ai_summary import AISummaryEnricher
from extractor.enrichers.join_relation import JoinRelationEnricher
from extractor.enrichers.column_semantic import ColumnSemanticEnricher
from extractor.enrichers.table_semantic import TableSemanticEnricher

__all__ = [
    "AISummaryEnricher",
    "JoinRelationEnricher",
    "ColumnSemanticEnricher",
    "TableSemanticEnricher",
]
