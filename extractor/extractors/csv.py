"""CSV extractor - Phase 1, single-node only"""
import os
import logging
from typing import List, Optional

from extractor.base import BaseExtractor
from common.config import Config
from common.schemas.csv import CSVNode

logger = logging.getLogger(__name__)


class CSVExtractor(BaseExtractor):
    """
    Extracts CSV file metadata.
    Rules:
    - Only extracts file-level stats (row count, column count, delimiter)
    - Does NOT parse/analyze column contents
    """

    @property
    def handles_types(self) -> List[str]:
        return ["CSV"]

    def can_extract(self, path: str) -> bool:
        """Check if file is a CSV"""
        if not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in self.config.csv_extensions

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[CSVNode]:
        """Extract CSV metadata only"""
        try:
            import csv

            name = os.path.basename(path)

            # Detect encoding and delimiter
            encoding = self._detect_encoding(path)
            delimiter = self._detect_delimiter(path, encoding)

            # Count rows and columns
            row_count = 0
            column_count = 0
            has_header = False

            with open(path, 'r', encoding=encoding, errors='replace') as f:
                reader = csv.reader(f, delimiter=delimiter)

                for i, row in enumerate(reader):
                    if i == 0:
                        column_count = len(row)
                        # Heuristic: if first row looks like headers
                        has_header = self._looks_like_header(row)
                    row_count += 1

            # Subtract header row if present
            data_rows = row_count - 1 if has_header else row_count

            return CSVNode(
                name=name,
                row_count=data_rows,
                column_count=column_count,
                delimiter=delimiter,
                has_header=has_header,
                encoding=encoding
            )

        except Exception as e:
            logger.error(f"Failed to extract CSV {path}: {e}")
            return None

    def _detect_encoding(self, path: str) -> str:
        """Detect file encoding"""
        try:
            import chardet
            with open(path, 'rb') as f:
                raw = f.read(10000)
                result = chardet.detect(raw)
                return result.get('encoding', 'utf-8') or 'utf-8'
        except ImportError:
            return 'utf-8'
        except Exception:
            return 'utf-8'

    def _detect_delimiter(self, path: str, encoding: str) -> str:
        """Detect CSV delimiter"""
        try:
            with open(path, 'r', encoding=encoding, errors='replace') as f:
                sample = f.read(2000)
                if '\t' in sample and sample.count('\t') > sample.count(','):
                    return '\t'
                return ','
        except Exception:
            return ','

    def _looks_like_header(self, row: List[str]) -> bool:
        """Heuristic: does this row look like a header?"""
        if not row:
            return False
        # Headers typically don't contain pure numbers
        has_numbers = sum(1 for cell in row if cell.isdigit())
        return has_numbers < len(row) / 2
