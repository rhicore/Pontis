"""Single-node extractors - Phase 1 of extraction

Each extractor handles one node type and only extracts self-metadata.
No recursion, no cross-node access.

To add a new extractor:
1. Create a file in this directory
2. Inherit from BaseExtractor
3. Implement handles_types, can_extract(), extract()
4. Register in ModularEngine
"""
from extractor.extractors.directory import DirectoryExtractor
from extractor.extractors.db import DBExtractor
from extractor.extractors.table import TableExtractor
from extractor.extractors.column import ColumnExtractor
from extractor.extractors.csv import CSVExtractor
from extractor.extractors.json import JsonExtractor
from extractor.extractors.markdown import MarkdownExtractor

__all__ = [
    "DirectoryExtractor",
    "DBExtractor",
    "TableExtractor",
    "ColumnExtractor",
    "CSVExtractor",
    "JsonExtractor",
    "MarkdownExtractor",
]
