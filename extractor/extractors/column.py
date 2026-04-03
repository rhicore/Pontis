"""Column extractor - Phase 1, single-node only"""
import json
import logging
from typing import Any, List, Optional

from extractor.base import BaseExtractor
from extractor.config import ExtractorConfig
from common.schemas.column import ColumnNode

logger = logging.getLogger(__name__)


class ColumnExtractor(BaseExtractor):
    """
    Extracts column metadata.
    Rules:
    - Only extracts this column's stats
    - Does NOT analyze cross-column relationships
    """

    @property
    def handles_types(self) -> List[str]:
        return ["Column"]

    def can_extract(self, path: str) -> bool:
        """Column extraction is triggered internally"""
        return False

    def extract(self, path: str, parent_rel_path: str = "") -> Optional[ColumnNode]:
        """Not used - use extract_column() instead"""
        return None

    def extract_column(
        self,
        conn,
        table_name: str,
        column_name: str,
        sql_type: str,
        notnull: bool,
        default: Any,
        is_pk: bool,
        table_row_count: Optional[int],
        dialect: str = "SQLite"
    ) -> Optional[ColumnNode]:
        """Extract column metadata from database"""
        try:
            cursor = conn.cursor()

            # Infer data type
            data_type = self._infer_data_type(sql_type)

            # Create node
            node = ColumnNode(
                name=column_name,
                data_type=data_type,
                nullable=not notnull
            )

            if is_pk:
                node.description = "Primary Key"

            # Skip stats if empty table
            if table_row_count == 0:
                node.cardinality = 0
                node.null_count = 0
                return node

            # Calculate stats
            self._calculate_stats(cursor, table_name, column_name, data_type, table_row_count, node)

            # Get samples as list
            try:
                node.samples = self._get_samples(cursor, table_name, column_name)[:5]
            except Exception as e:
                logger.debug(f"Could not get samples: {e}")
                node.samples = []

            return node

        except Exception as e:
            logger.error(f"Failed to extract column {table_name}.{column_name}: {e}")
            return None

    def _infer_data_type(self, sql_type: str) -> str:
        """Infer data type from SQL type"""
        sql_type_upper = (sql_type or "").upper()

        if any(t in sql_type_upper for t in ['INT', 'SERIAL', 'BIGINT']):
            return "INTEGER"
        elif any(t in sql_type_upper for t in ['REAL', 'FLOAT', 'DOUBLE', 'DECIMAL']):
            return "REAL"
        elif any(t in sql_type_upper for t in ['TEXT', 'CLOB', 'CHAR', 'VARCHAR']):
            return "TEXT"
        elif any(t in sql_type_upper for t in ['BLOB', 'BINARY']):
            return "BLOB"
        elif 'JSON' in sql_type_upper:
            return "JSON"
        elif 'BOOLEAN' in sql_type_upper or 'BOOL' in sql_type_upper:
            return "BOOLEAN"
        elif any(t in sql_type_upper for t in ['DATE', 'TIME']):
            return "DATETIME"
        return "UNKNOWN"

    def _calculate_stats(
        self, cursor, table_name: str, column_name: str,
        data_type: str, table_row_count: Optional[int], node: ColumnNode
    ):
        """Calculate column statistics"""
        # Cardinality
        try:
            cursor.execute(f'''
                SELECT COUNT(DISTINCT "{column_name}")
                FROM "{table_name}"
                WHERE "{column_name}" IS NOT NULL
            ''')
            node.cardinality = cursor.fetchone()[0]
        except Exception as e:
            logger.debug(f"Could not get cardinality: {e}")

        # Null count
        try:
            cursor.execute(f'''
                SELECT COUNT(*) FROM "{table_name}" WHERE "{column_name}" IS NULL
            ''')
            null_count = cursor.fetchone()[0]
            node.null_count = null_count
            if table_row_count and table_row_count > 0:
                node.null_percentage = (null_count / table_row_count) * 100
        except Exception as e:
            logger.debug(f"Could not get null count: {e}")

        # Type-specific stats
        if data_type in ["INTEGER", "REAL"]:
            try:
                cursor.execute(f'''
                    SELECT MIN("{column_name}"), MAX("{column_name}"), AVG("{column_name}")
                    FROM "{table_name}" WHERE "{column_name}" IS NOT NULL
                ''')
                row = cursor.fetchone()
                if row:
                    node.min_value = row[0]
                    node.max_value = row[1]
                    node.mean_value = row[2]
            except Exception as e:
                logger.debug(f"Could not get numeric stats: {e}")

        elif data_type == "TEXT":
            try:
                cursor.execute(f'''
                    SELECT MIN(LENGTH("{column_name}")), MAX(LENGTH("{column_name}")), AVG(LENGTH("{column_name}"))
                    FROM "{table_name}" WHERE "{column_name}" IS NOT NULL
                ''')
                row = cursor.fetchone()
                if row:
                    node.min_length = row[0]
                    node.max_length = row[1]
                    node.avg_length = row[2]
            except Exception as e:
                logger.debug(f"Could not get string stats: {e}")

        # Top K
        try:
            cursor.execute(f'''
                SELECT "{column_name}", COUNT(*) as cnt
                FROM "{table_name}"
                WHERE "{column_name}" IS NOT NULL
                GROUP BY "{column_name}"
                ORDER BY cnt DESC
                LIMIT {self.config.top_k}
            ''')
            node.top_k = [{"value": row[0], "count": row[1]} for row in cursor.fetchall()]
        except Exception as e:
            logger.debug(f"Could not get top k: {e}")
            node.top_k = []

    def _get_samples(self, cursor, table_name: str, column_name: str) -> List[Any]:
        """Get sample values"""
        cursor.execute(f'''
            SELECT DISTINCT "{column_name}"
            FROM "{table_name}"
            WHERE "{column_name}" IS NOT NULL
            LIMIT 5
        ''')
        return [row[0] for row in cursor.fetchall()]
