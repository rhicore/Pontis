"""BIRD official database description extractor.

This deterministic pass imports human-authored BIRD
``database_description/*.csv`` annotations onto existing database column
entities. It intentionally does not rewrite AI-authored ``brief`` or
``detail`` fields, and it does not create new entities.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

_DESCRIPTION_DIR = "database_description"
_COLUMN_DESCRIPTION = "official_column_description"
_VALUE_DESCRIPTION = "official_value_description"


def generate(workspace: Workspace) -> None:
    """Import official column/value descriptions from database_description CSVs."""
    project_path = Path(workspace.project_path)
    project_root = project_path.parent if project_path.is_file() else project_path
    description_dir = project_root / _DESCRIPTION_DIR
    if not description_dir.is_dir():
        logger.info("=== BIRD official description extract: no database_description directory ===")
        return None

    logger.info("=== BIRD official description extract ===")
    total_rows = 0
    updated = 0
    missing = 0

    for csv_path in sorted(description_dir.glob("*.csv")):
        table_name = csv_path.stem
        rows = _read_description_rows(csv_path)
        total_rows += len(rows)
        table_updated = 0
        table_missing = 0
        for row in rows:
            column_name = _clean(row.get("original_column_name"))
            if not column_name:
                continue
            props = {
                _COLUMN_DESCRIPTION: _clean(row.get("column_description")),
                _VALUE_DESCRIPTION: _clean(row.get("value_description")),
            }
            if _update_column(workspace, table_name, column_name, props):
                updated += 1
                table_updated += 1
            else:
                missing += 1
                table_missing += 1
                logger.debug(
                    "Official description target not found: %s.%s",
                    table_name,
                    column_name,
                )
        logger.info(
            "  %s: updated %s columns, missing %s",
            csv_path.name,
            table_updated,
            table_missing,
        )

    logger.info(
        "BIRD official description extract done: updated %s/%s columns, missing %s",
        updated,
        total_rows,
        missing,
    )
    removed = _remove_description_source_nodes(workspace)
    if removed:
        logger.info("Removed %s legacy database_description graph nodes", removed)
    return None


def _remove_description_source_nodes(workspace: Workspace) -> int:
    """Remove the one-shot import source from the graph after migration."""
    rows = workspace.cypher(
        "MATCH (n) "
        "WHERE n.path = $dir OR n.path STARTS WITH $path_prefix "
        "   OR n._ref STARTS WITH $ref_prefix "
        "WITH collect(n) AS nodes, count(n) AS removed "
        "FOREACH (n IN nodes | DETACH DELETE n) "
        "RETURN removed",
        params={
            "dir": _DESCRIPTION_DIR,
            "path_prefix": f"{_DESCRIPTION_DIR}/",
            "ref_prefix": f"{_DESCRIPTION_DIR}/",
        },
    )
    return int(rows[0].get("removed", 0)) if rows else 0


def _read_description_rows(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _update_column(
    workspace: Workspace,
    table_name: str,
    column_name: str,
    props: dict[str, str],
) -> bool:
    rows = workspace.cypher(
        "MATCH (t:table {name: $table_name})--(c:col {name: $column_name}) "
        "SET c += $props "
        "RETURN c",
        params={
            "table_name": table_name,
            "column_name": column_name,
            "props": props,
        },
    )
    return bool(rows)
