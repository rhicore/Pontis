"""Directory extractor - Phase 1, single-node only"""
import os
import logging
from typing import List, Optional

from extractor.base import BaseExtractor
from common.config import Config
from common.schemas.directory import DirectoryNode

logger = logging.getLogger(__name__)


class DirectoryExtractor(BaseExtractor):
    """
    Extracts directory metadata.
    Rules:
    - Only counts direct children (files + subdirs)
    - Does NOT recurse into subdirectories
    - Does NOT read file contents
    """

    @property
    def handles_types(self) -> List[str]:
        return ["Directory"]

    def can_extract(self, path: str) -> bool:
        """Can extract any directory"""
        return os.path.isdir(path)

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[DirectoryNode]:
        """Extract directory metadata only (no recursion)"""
        try:
            name = os.path.basename(path) or "."

            # Count direct children only
            file_count = 0
            subdir_count = 0

            try:
                for entry in os.listdir(path):
                    if entry.startswith('.'):
                        continue
                    full_path = os.path.join(path, entry)
                    if os.path.isdir(full_path):
                        subdir_count += 1
                    else:
                        file_count += 1
            except PermissionError:
                logger.warning(f"Permission denied: {path}")

            return DirectoryNode(
                name=name,
                file_count=file_count,
                subdir_count=subdir_count,
                child_count=file_count + subdir_count
            )

        except Exception as e:
            logger.error(f"Failed to extract directory {path}: {e}")
            return None
