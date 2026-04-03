"""Database extractor - Phase 1, single-node only

NOTE: This extractor ONLY creates the DB node.
Table/Column extraction is handled by a separate pass or by the engine.
"""
import os
import logging
from typing import List, Optional

from extractor.base import BaseExtractor
from extractor.config import ExtractorConfig
from common.schemas.db import DBNode

logger = logging.getLogger(__name__)


class DBExtractor(BaseExtractor):
    """
    Extracts database metadata.
    Rules:
    - Only extracts DB-level info (dialect, table count, view count)
    - Does NOT extract table schemas
    - Does NOT extract column stats
    """

    @property
    def handles_types(self) -> List[str]:
        return ["DB"]

    def can_extract(self, path: str) -> bool:
        """Check if file is a supported database"""
        if not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in self.config.db_extensions

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[DBNode]:
        """Extract database metadata only (no tables/columns)"""
        try:
            name = os.path.basename(path)
            dialect = self._detect_dialect(path)

            # Connect and get counts only
            conn = self._connect(path, dialect)
            if not conn:
                return DBNode(
                    name=name,
                    dialect=dialect,
                    description="Failed to connect"
                )

            try:
                table_count, view_count = self._get_counts(conn, dialect)
            finally:
                conn.close()

            return DBNode(
                name=name,
                dialect=dialect,
                table_count=table_count,
                view_count=view_count
            )

        except Exception as e:
            logger.error(f"Failed to extract DB {path}: {e}")
            return None

    def _detect_dialect(self, path: str) -> str:
        """Detect database dialect from extension"""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".duckdb":
            return "DuckDB"
        return "SQLite"

    def _connect(self, path: str, dialect: str):
        """Connect to database"""
        try:
            if dialect == "SQLite":
                import sqlite3
                return sqlite3.connect(path)
            elif dialect == "DuckDB":
                import duckdb
                return duckdb.connect(path)
        except Exception as e:
            logger.error(f"Failed to connect to {dialect}: {e}")
            return None

    def _get_counts(self, conn, dialect: str) -> tuple:
        """Get table and view counts"""
        cursor = conn.cursor()

        if dialect == "SQLite":
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            table_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
            view_count = cursor.fetchone()[0]
        else:
            table_count = 0
            view_count = 0

        return table_count, view_count

    def expand(self, node: DBNode, physical_path: str, pontis_path: str):
        """
        Expand DB node into Table/View child nodes.
        Called by engine after DB node is extracted.
        """
        from extractor.extractors.table import TableExtractor

        children = []
        dialect = node.dialect

        # Connect to database
        conn = self._connect(physical_path, dialect)
        if not conn:
            return children

        try:
            table_extractor = TableExtractor(self.config)
            cursor = conn.cursor()

            # Get tables
            if dialect == "SQLite":
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            for (table_name,) in cursor.fetchall():
                try:
                    table_node = table_extractor.extract_table(conn, table_name, is_view=False, dialect=dialect)
                    if table_node:
                        children.append((table_node, table_name))
                except Exception as e:
                    logger.warning(f"Failed to extract table {table_name}: {e}")

            # Get views
            if dialect == "SQLite":
                cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
            for (view_name,) in cursor.fetchall():
                try:
                    view_node = table_extractor.extract_table(conn, view_name, is_view=True, dialect=dialect)
                    if view_node:
                        children.append((view_node, view_name))
                except Exception as e:
                    logger.warning(f"Failed to extract view {view_name}: {e}")

        finally:
            conn.close()

        return children
