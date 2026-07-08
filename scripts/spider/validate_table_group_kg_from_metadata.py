#!/usr/bin/env python3
"""Build a metadata-backed table-group proof graph for Spider2-Snow.

This script is a debugging/validation entry point, not the production
extractor. It loads Spider2-Snow local table JSON metadata, writes a minimal
``db -> schema -> table -> col`` graph, then reuses the extractor table-group
summary/upsert functions to verify the KG node shape without forcing a live
Snowflake metadata refresh.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from extractor import db_table_group
from explorer import schema_landscape
from scripts.spider.common import (
    SPIDER2_SNOW_DATABASES,
    get_project_dir,
    group_cases_by_db,
    load_spider2_snow_cases,
    prepare_spider2_snow_project,
    parse_csv_arg,
)
from storage.workspace import Workspace


def _schema_and_table(data: dict, path: Path) -> tuple[str, str]:
    full_name = str(data.get("table_fullname") or data.get("table_name") or "")
    parts = full_name.split(".")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1]

    rel = path.relative_to(SPIDER2_SNOW_DATABASES)
    if len(rel.parts) >= 3:
        return rel.parts[1], path.stem
    return "", path.stem


def _load_metadata_tables(db_id: str) -> list[db_table_group.TableInfo]:
    db_dir = SPIDER2_SNOW_DATABASES / db_id
    if not db_dir.exists():
        raise FileNotFoundError(f"Spider2-Snow database metadata not found: {db_dir}")

    tables: list[db_table_group.TableInfo] = []
    for path in sorted(db_dir.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        schema_name, table_name = _schema_and_table(data, path)
        schema_ref = f"{db_id}--{schema_name}" if schema_name else f"{db_id}--_default"
        table_ref = f"{schema_ref}--{table_name}"
        family, pattern_types = db_table_group._table_family(table_name)
        columns = tuple(str(name) for name in data.get("column_names") or [] if name)
        tables.append(
            db_table_group.TableInfo(
                db_ref=db_id,
                table_ref=table_ref,
                schema_ref=schema_ref,
                database_name=db_id,
                schema_name=schema_name,
                table_name=table_name,
                family=family,
                pattern_types=pattern_types,
                columns=columns,
            )
        )
    return tables


def _ensure_project(db_id: str, *, force_prepare: bool) -> Path:
    project_dir = get_project_dir(db_id)
    if project_dir.exists() and not force_prepare:
        return project_dir
    cases = group_cases_by_db(load_spider2_snow_cases()).get(db_id, [])
    prepare_spider2_snow_project(db_id, cases, force=force_prepare)
    return project_dir


def _store(workspace: Workspace):
    if not workspace.active_projects:
        raise RuntimeError("Workspace has no active project")
    store = workspace._get_store(workspace.active_projects[0])
    if store is None:
        raise RuntimeError("Workspace store not found")
    return store


def _write_minimal_schema_graph(workspace: Workspace, db_id: str, tables: list[db_table_group.TableInfo]) -> None:
    store = _store(workspace)
    project = workspace.active_projects[0]
    _create_validation_indexes(store)
    _clear_project_graph(store, project)
    store.execute_cypher(
        """
        CREATE (d:db:snowflake {
            _ref: $db_ref,
            name: $db_ref,
            database_name: $db_ref,
            dialect: 'snowflake',
            path: $db_ref,
            project: $project,
            labels: ['db', 'snowflake']
        })
        """,
        params={"db_ref": db_id, "project": project},
    )

    schemas = sorted({(table.schema_ref, table.schema_name) for table in tables})
    for batch in _batches(schemas, 100):
        store.execute_cypher(
            """
            MATCH (d:db {_ref: $db_ref})
            UNWIND $rows AS row
            CREATE (s:schema {
                _ref: row.ref,
                name: row.name,
                schema_name: row.name,
                database_name: $db_ref,
                _db_ref: $db_ref,
                project: $project,
                labels: ['schema']
            })
            CREATE (d)-[:RELATED_TO]->(s)
            """,
            params={
                "db_ref": db_id,
                "project": project,
                "rows": [{"ref": ref, "name": name} for ref, name in batch],
            },
        )

    table_rows = [
        {
            "ref": table.table_ref,
            "schema_ref": table.schema_ref,
            "name": table.table_name,
            "schema_name": table.schema_name,
            "columns": [
                {
                    "ref": f"{table.table_ref}--{column}",
                    "name": column,
                    "ordinal": index + 1,
                }
                for index, column in enumerate(table.columns)
            ],
        }
        for table in tables
    ]
    for batch in _batches(table_rows, 20):
        store.execute_cypher(
            """
            UNWIND $rows AS row
            MATCH (s:schema {_ref: row.schema_ref})
            CREATE (t:table {
                _ref: row.ref,
                name: row.name,
                table_name: row.name,
                schema_name: row.schema_name,
                database_name: $db_ref,
                _db_ref: $db_ref,
                _schema_ref: row.schema_ref,
                project: $project,
                labels: ['table']
            })
            CREATE (s)-[:RELATED_TO]->(t)
            WITH t, row
            UNWIND row.columns AS col
            CREATE (c:col {
                _ref: col.ref,
                name: col.name,
                column_name: col.name,
                ordinal_position: col.ordinal,
                table_name: row.name,
                schema_name: row.schema_name,
                database_name: $db_ref,
                _db_ref: $db_ref,
                _table_ref: row.ref,
                project: $project,
                labels: ['col']
            })
            CREATE (t)-[:RELATED_TO]->(c)
            """,
            params={"db_ref": db_id, "project": project, "rows": batch},
        )


def _create_validation_indexes(store) -> None:
    for label in ("db", "schema", "table", "col", "table_group"):
        safe_label = "".join(ch for ch in label if ch.isalnum() or ch == "_")
        store.execute_cypher(
            f"CREATE INDEX pontis_validation_{safe_label}_ref IF NOT EXISTS FOR (n:{safe_label}) ON (n._ref)"
        )


def _clear_project_graph(store, project: str, *, batch_size: int = 1000) -> None:
    while True:
        rows = store.execute_cypher(
            """
            MATCH (n {project: $project})
            WITH n LIMIT $batch_size
            DETACH DELETE n
            RETURN count(n) AS deleted
            """,
            params={"project": project, "batch_size": batch_size},
        )
        deleted = int((rows[0] if rows else {}).get("deleted") or 0)
        if deleted == 0:
            return


def _build_table_groups(
    workspace: Workspace,
    tables: list[db_table_group.TableInfo],
    *,
    min_members: int,
    include_singletons: bool,
) -> list[dict]:
    grouped: dict[tuple[str, str, str, str], list[db_table_group.TableInfo]] = defaultdict(list)
    for table in tables:
        if not include_singletons and table.family == table.table_name.upper():
            continue
        grouped[(table.db_ref, table.schema_ref, table.schema_name, table.family)].append(table)

    summaries = [
        db_table_group._group_summary(key, members)
        for key, members in grouped.items()
        if len(members) >= max(1, min_members)
    ]
    summaries.sort(key=lambda item: (-item["member_count"], item["db_ref"], item["schema_name"], item["family"]))
    db_table_group._delete_stale_groups(workspace, grouping_method="pattern_table_family_v1")
    for summary in summaries:
        db_table_group._upsert_group(workspace, _copy_summary(summary))
    return summaries


def _copy_summary(summary: dict) -> dict:
    copied = {}
    for key, value in summary.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


def _batches(items, size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _count_schema_landscapes(workspace: Workspace) -> int:
    store = _store(workspace)
    project = workspace.active_projects[0]
    rows = store.execute_cypher(
        "MATCH (l:schema_landscape {project: $project}) RETURN count(l) AS c",
        params={"project": project},
    )
    return int((rows[0] if rows else {}).get("c") or 0)


def _print_report(
    db_id: str,
    tables: list[db_table_group.TableInfo],
    table_groups: list[dict],
    landscape_count: int,
) -> None:
    table_count = len(tables)
    column_count = sum(len(table.columns) for table in tables)
    grouped_table_count = sum(group["member_count"] for group in table_groups)
    print(f"Spider2-Snow metadata KG validation: {db_id}")
    print(f"tables: {table_count}")
    print(f"columns: {column_count}")
    print(f"table_groups: {len(table_groups)}")
    print(f"grouped_tables: {grouped_table_count}/{table_count} ({grouped_table_count / table_count:.1%})")
    print(f"schema_landscapes: {landscape_count}")

    print("\nTop table groups")
    for group in table_groups[:20]:
        print(
            f"- {group['schema_name']}.{group['family']}: "
            f"members={group['member_count']}, consistency={group['consistency']}, "
            f"common_cols={group['common_column_count']}, union_cols={group['union_column_count']}, "
            f"examples={group['sample_members']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="FEC", help="Comma-separated Spider2-Snow database ids.")
    parser.add_argument("--min-members", type=int, default=3)
    parser.add_argument("--include-singletons", action="store_true")
    parser.add_argument("--force-prepare", action="store_true", help="Recreate the local Pontis project snapshot first.")
    args = parser.parse_args()

    db_ids = parse_csv_arg(args.db) or ["FEC"]
    for db_id in db_ids:
        project_dir = _ensure_project(db_id, force_prepare=args.force_prepare)
        workspace = Workspace(project_path=str(project_dir))
        tables = _load_metadata_tables(db_id)
        _write_minimal_schema_graph(workspace, db_id, tables)
        table_groups = _build_table_groups(
            workspace,
            tables,
            min_members=max(1, args.min_members),
            include_singletons=args.include_singletons,
        )
        schema_landscape.generate(workspace)
        _print_report(db_id, tables, table_groups, _count_schema_landscapes(workspace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
