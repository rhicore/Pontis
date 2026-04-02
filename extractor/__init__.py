"""Pontis Metadata Extractor - Two-phase architecture

Phase 1 (extractors/): Single-node extraction
- Each extractor handles one node type
- No cross-node access
- Generates basic metadata

Phase 2 (enrichers/): Cross-node enrichment
- Full tree access via NodeTree
- Can analyze relationships between nodes
- Generates derived metadata (joins, AI summaries)

Usage:
    from extractor import ModularEngine

    engine = ModularEngine()
    engine.extract("./my_data")

Adding custom extractors:
    class MyExtractor(BaseExtractor):
        @property
        def handles_types(self): return ["MyType"]
        def can_extract(self, path): ...
        def extract(self, path, parent_rel_path): ...

    engine.register_extractor(MyExtractor(config))

Adding custom enrichers:
    class MyEnricher(BaseEnricher):
        @property
        def priority(self): return 600
        def should_run(self, tree): return True
        def enrich(self, tree): ...

    engine.register_enricher(MyEnricher(config))
"""
from extractor.base import BaseExtractor, BaseEnricher, NodeTree
from extractor.engine import ExtractorEngine, ModularEngine

__all__ = [
    # Base classes
    "BaseExtractor",
    "BaseEnricher",
    "NodeTree",
    # Engines
    "ExtractorEngine",
    "ModularEngine",
]
