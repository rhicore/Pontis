"""Table extractor - Phase 1, single-node only

This extractor is typically called internally by DBExtractor
after the initial DB node is created.
"""
import os
import logging
from typing import List, Optional

from extractor.base import BaseExtractor
from common.config import Config
from common.schemas.table import TableNode, ViewNode

logger = logging.getLogger(__name__)


class TableExtractor(BaseExtractor):
    """
    Extracts table/view metadata.
    Rules:
    - Only extracts table-level info (row count, column count, PK)
    - Does NOT extract column stats
    """

    @property
    def handles_types(self) -> List[str]:
        return ["Table", "View"]

    def can_extract(self, path: str) -> bool:
        """Table extraction is triggered internally, not by file detection"""
        return False

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[TableNode]:
        """Not used - use extract_table() instead"""
        return None

    def extract_table(
        self,
        conn,
        table_name: str,
        is_view: bool = False,
        dialect: str = "SQLite"
    ) -> Optional[TableNode]:
        """
        Extract table metadata from database connection.
        This is called internally by DBExtractor or engine.
        """
        try:
            cursor = conn.cursor()

            # Get row count
            try:
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                row_count = cursor.fetchone()[0]
            except Exception as e:
                logger.warning(f"Failed to get row count for {table_name}: {e}")
                row_count = None

            # Get column count and PK
            columns_info, pk_columns = self._get_column_info(cursor, table_name, dialect)

            # Get view definition if applicable
            view_definition = None
            base_tables = []
            if is_view:
                view_definition = self._get_view_definition(cursor, table_name, dialect)

            # Create node
            if is_view:
                return ViewNode(
                    name=table_name,
                    row_count=row_count,
                    column_count=len(columns_info),
                    primary_key=pk_columns[0] if pk_columns else None,
                    base_tables=base_tables,
                    view_definition=view_definition
                )
            else:
                return TableNode(
                    name=table_name,
                    row_count=row_count,
                    column_count=len(columns_info),
                    primary_key=pk_columns[0] if pk_columns else None
                )

        except Exception as e:
            logger.error(f"Failed to extract table {table_name}: {e}")
            return None

    def _get_column_info(self, cursor, table_name: str, dialect: str) -> tuple:
        """Get column info and PK columns"""
        columns_info = []
        pk_columns = []

        try:
            if dialect == "SQLite":
                cursor.execute(f'PRAGMA table_info("{table_name}")')
                for col in cursor.fetchall():
                    columns_info.append(col)
                    if col[5] == 1:  # pk flag
                        pk_columns.append(col[1])
        except Exception as e:
            logger.warning(f"Failed to get column info: {e}")

        return columns_info, pk_columns

    def _get_view_definition(self, cursor, view_name: str, dialect: str) -> Optional[str]:
        """Get view definition SQL"""
        try:
            if dialect == "SQLite":
                cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type='view' AND name=?",
                    (view_name,)
                )
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.warning(f"Failed to get view definition: {e}")
        return None

    def expand(self, node: TableNode, physical_path: str, pontis_path: str):
        """
        Expand Table/View node into Column child nodes.
        Called by engine after table node is extracted.
        """
        from extractor.extractors.column import ColumnExtractor

        children = []

        # Need to connect to parent DB to get column info
        # physical_path points to the DB file, table name is in node.name
        db_path = physical_path
        table_name = node.name
        dialect = "SQLite"  # Default, should be passed from parent

        # Try to infer dialect from file extension
        if db_path.endswith('.duckdb'):
            dialect = "DuckDB"

        # Connect to database
        try:
            if dialect == "SQLite":
                import sqlite3
                conn = sqlite3.connect(db_path)
            elif dialect == "DuckDB":
                import duckdb
                conn = duckdb.connect(db_path)
            else:
                return children
        except Exception as e:
            logger.warning(f"Failed to connect to expand table {table_name}: {e}")
            return children

        try:
            column_extractor = ColumnExtractor(self.config)
            cursor = conn.cursor()

            # Get column info
            if dialect == "SQLite":
                cursor.execute(f'PRAGMA table_info("{table_name}")')
                columns_info = cursor.fetchall()
            else:
                columns_info = []

            for col_info in columns_info:
                col_name = col_info[1]
                col_type = col_info[2]
                notnull = col_info[3]
                default = col_info[4]
                is_pk = col_info[5] == 1

                try:
                    col_node = column_extractor.extract_column(
                        conn, table_name, col_name, col_type,
                        notnull, default, is_pk, node.row_count, dialect
                    )
                    if col_node:
                        children.append((col_node, col_name))
                except Exception as e:
                    logger.warning(f"Failed to extract column {col_name}: {e}")

        finally:
            conn.close()

        return children
