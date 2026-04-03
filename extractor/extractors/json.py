"""JSON extractor - Phase 1, single-node only"""
import os
import json
import logging
from typing import List, Optional

from extractor.base import BaseExtractor
from extractor.config import ExtractorConfig
from common.schemas.json import JsonNode

logger = logging.getLogger(__name__)


class JsonExtractor(BaseExtractor):
    """
    Extracts JSON file metadata.
    Rules:
    - Only extracts structural info (record count, top-level keys)
    - Does NOT deeply parse nested structures
    """

    @property
    def handles_types(self) -> List[str]:
        return ["JSON"]

    def can_extract(self, path: str) -> bool:
        """Check if file is JSON"""
        if not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in self.config.json_extensions

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[JsonNode]:
        """Extract JSON metadata only"""
        try:
            name = os.path.basename(path)

            # Read and parse
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                data = json.load(f)

            # Analyze structure
            is_array = isinstance(data, list)
            record_count = len(data) if is_array else 1
            top_level_keys = list(data[0].keys()) if is_array and data else list(data.keys()) if isinstance(data, dict) else []

            return JsonNode(
                name=name,
                record_count=record_count,
                is_array=is_array,
                top_level_keys=top_level_keys[:20]  # Limit keys
            )

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON {path}: {e}")
            return JsonNode(
                name=os.path.basename(path),
                record_count=0,
                is_array=False,
                description="Invalid JSON"
            )
        except Exception as e:
            logger.error(f"Failed to extract JSON {path}: {e}")
            return None
