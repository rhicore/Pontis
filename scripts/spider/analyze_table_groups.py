#!/usr/bin/env python3
"""Detect table families in Spider2-Snow database metadata.

The script reads Spider2-Snow table JSON files and groups physical tables that
look like date/year/quarter/version/chromosome shards of the same logical table.
It does not connect to Snowflake.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from scripts.spider.common import SPIDER2_SNOW_DATABASES, parse_csv_arg


_YEAR_RE = re.compile(r"(17|18|19|20)\d{2}")
_YYYYMM_RE = re.compile(r"(17|18|19|20)\d{4}")
_YYYYMMDD_RE = re.compile(r"(17|18|19|20)\d{6}")
_COMPACT_YY_RE = re.compile(r"^([A-Z][A-Z0-9]*?[A-Z])(\d{2})$")
_COMPACT_PREFIX_DIGIT_YY_RE = re.compile(r"^([A-Z][A-Z0-9]*\d)(\d{2})$")
_GEO_SUFFIXES = {
    "ALABAMA",
    "ALASKA",
    "AMERICAN_SAMOA",
    "ARIZONA",
    "ARKANSAS",
    "CALIFORNIA",
    "COLORADO",
    "CONNECTICUT",
    "DELAWARE",
    "DISTRICT_OF_COLUMBIA",
    "FLORIDA",
    "GEORGIA",
    "GUAM",
    "HAWAII",
    "IDAHO",
    "ILLINOIS",
    "INDIANA",
    "IOWA",
    "KANSAS",
    "KENTUCKY",
    "LOUISIANA",
    "MAINE",
    "MARYLAND",
    "MASSACHUSETTS",
    "MICHIGAN",
    "MINNESOTA",
    "MISSISSIPPI",
    "MISSOURI",
    "MONTANA",
    "NEBRASKA",
    "NEVADA",
    "NEW_HAMPSHIRE",
    "NEW_JERSEY",
    "NEW_MEXICO",
    "NEW_YORK",
    "NORTH_CAROLINA",
    "NORTH_DAKOTA",
    "NORTHERN_MARIANA_ISLANDS",
    "OHIO",
    "OKLAHOMA",
    "OREGON",
    "PENNSYLVANIA",
    "PUERTO_RICO",
    "RHODE_ISLAND",
    "SOUTH_CAROLINA",
    "SOUTH_DAKOTA",
    "TENNESSEE",
    "TEXAS",
    "UTAH",
    "VERMONT",
    "VIRGIN_ISLANDS",
    "VIRGINIA",
    "WASHINGTON",
    "WEST_VIRGINIA",
    "WISCONSIN",
    "WYOMING",
}
_GEO_SUFFIX_PATTERN = re.compile(rf"_(?:{'|'.join(sorted(_GEO_SUFFIXES, key=len, reverse=True))})$")


def table_family_name(table_name: str) -> str:
    """Return a conservative family key for partition/version table names."""

    name = str(table_name or "").upper()
    # Replace longer date tokens first. These also handle names like GSOD1955
    # and GA_SESSIONS_20170101.
    name = _YYYYMMDD_RE.sub("YYYYMMDD", name)
    name = _YYYYMM_RE.sub("YYYYMM", name)
    name = _YEAR_RE.sub("YYYY", name)

    # Common Spider2-Snow physical sharding/version suffixes.
    name = re.sub(r"^REL\d+(?=_|$)", "REL#", name)
    name = re.sub(r"_R\d+(?=_|$)", "_R#", name)
    name = re.sub(r"_Q[1-4]\b", "_Q#", name)
    name = re.sub(r"__CHR(?:\d+|X|Y|MT|M)(?=_|$)", "__CHR#", name)
    name = re.sub(r"_CHR(?:\d+|X|Y|MT|M)(?=_|$)", "_CHR#", name)
    name = re.sub(r"_\d{1,3}\b", "_#", name)
    name = _GEO_SUFFIX_PATTERN.sub("_GEO_REGION", name)
    name = _compact_year_family(name)
    return name


def _compact_year_family(name: str) -> str:
    """Normalize compact FEC-style cycle suffixes such as INDIV20 or PAS220."""

    if "_" in name or len(name) < 4:
        return name
    match = _COMPACT_PREFIX_DIGIT_YY_RE.fullmatch(name)
    if match:
        return f"{match.group(1)}YY"
    match = _COMPACT_YY_RE.fullmatch(name)
    if match:
        return f"{match.group(1)}YY"
    return name


def _schema_and_table(data: dict, path: Path) -> tuple[str, str]:
    full_name = str(data.get("table_fullname") or "")
    parts = full_name.split(".")
    if len(parts) >= 3:
        return parts[-2], parts[-1]

    rel = path.relative_to(SPIDER2_SNOW_DATABASES)
    if len(rel.parts) >= 3:
        return rel.parts[1], path.stem
    return "", path.stem


def _load_tables(db_filter: set[str] | None = None) -> dict[str, list[dict]]:
    tables_by_db: dict[str, list[dict]] = defaultdict(list)
    for path in SPIDER2_SNOW_DATABASES.rglob("*.json"):
        rel = path.relative_to(SPIDER2_SNOW_DATABASES)
        if not rel.parts:
            continue
        db_id = rel.parts[0]
        if db_filter and db_id not in db_filter:
            continue
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        schema_name, table_name = _schema_and_table(data, path)
        columns = [str(item) for item in data.get("column_names") or []]
        tables_by_db[db_id].append({
            "db_id": db_id,
            "schema": schema_name,
            "table": table_name,
            "family": table_family_name(table_name),
            "columns": columns,
            "path": str(rel),
            "sample_rows": len(data.get("sample_rows") or []),
        })
    return dict(sorted(tables_by_db.items()))


def _column_signature(columns: Iterable[str], *, ordered: bool) -> tuple[str, ...]:
    normalized = [str(col).lower() for col in columns]
    return tuple(normalized if ordered else sorted(normalized))


def _group_summary(key: tuple[str, str], members: list[dict]) -> dict:
    ordered_signatures = Counter(
        _column_signature(member["columns"], ordered=True)
        for member in members
    )
    set_signatures = Counter(
        _column_signature(member["columns"], ordered=False)
        for member in members
    )
    column_sets = [set(_column_signature(member["columns"], ordered=False)) for member in members]
    common_columns = set.intersection(*column_sets) if column_sets else set()
    union_columns = set.union(*column_sets) if column_sets else set()
    member_column_counts = Counter(len(member["columns"]) for member in members)
    same_order = len(ordered_signatures) == 1
    same_set = len(set_signatures) == 1
    consistency = "same_order" if same_order else "same_set" if same_set else "drifting"
    return {
        "schema": key[0],
        "family": key[1],
        "member_count": len(members),
        "members": [member["table"] for member in sorted(members, key=lambda item: item["table"])],
        "column_count_distribution": dict(sorted(member_column_counts.items())),
        "same_order_columns": same_order,
        "same_column_set": same_set,
        "column_set_signatures": len(set_signatures),
        "common_column_count": len(common_columns),
        "union_column_count": len(union_columns),
        "variable_column_count": len(union_columns - common_columns),
        "consistency": consistency,
        "sample_members": [member["table"] for member in sorted(members, key=lambda item: item["table"])[:8]],
    }


def analyze_table_groups(db_filter: set[str] | None = None, *, min_members: int = 3) -> dict:
    tables_by_db = _load_tables(db_filter)
    databases = []
    all_groups = []
    for db_id, tables in tables_by_db.items():
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for table in tables:
            if table["family"] == table["table"].upper():
                continue
            grouped[(table["schema"], table["family"])].append(table)
        groups = [
            _group_summary(key, members)
            for key, members in grouped.items()
            if len(members) >= min_members
        ]
        groups.sort(key=lambda item: (-item["member_count"], item["schema"], item["family"]))
        grouped_table_count = sum(group["member_count"] for group in groups)
        db_summary = {
            "db_id": db_id,
            "table_count": len(tables),
            "column_count": sum(len(table["columns"]) for table in tables),
            "group_count": len(groups),
            "grouped_table_count": grouped_table_count,
            "grouped_table_ratio": grouped_table_count / len(tables) if tables else 0.0,
            "same_column_set_group_count": sum(1 for group in groups if group["same_column_set"]),
            "drifting_group_count": sum(1 for group in groups if not group["same_column_set"]),
            "groups": groups,
        }
        databases.append(db_summary)
        for group in groups:
            all_groups.append({"db_id": db_id, **group})

    databases.sort(key=lambda item: (-item["grouped_table_count"], item["db_id"]))
    all_groups.sort(key=lambda item: (-item["member_count"], item["db_id"], item["schema"], item["family"]))
    return {
        "min_members": min_members,
        "database_count": len(databases),
        "table_count": sum(item["table_count"] for item in databases),
        "column_count": sum(item["column_count"] for item in databases),
        "databases_with_groups": sum(1 for item in databases if item["group_count"] > 0),
        "group_count": len(all_groups),
        "grouped_table_count": sum(item["member_count"] for item in all_groups),
        "same_column_set_group_count": sum(1 for item in all_groups if item["same_column_set"]),
        "drifting_group_count": sum(1 for item in all_groups if not item["same_column_set"]),
        "no_group_databases": sorted(
            [
                {
                    "db_id": item["db_id"],
                    "table_count": item["table_count"],
                    "column_count": item["column_count"],
                }
                for item in databases
                if item["group_count"] == 0
            ],
            key=lambda item: (-item["table_count"], -item["column_count"], item["db_id"]),
        ),
        "databases": databases,
        "groups": all_groups,
    }


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _print_report(result: dict, *, top_dbs: int, top_groups: int, details: bool) -> None:
    print("Spider2-Snow table group analysis")
    print(f"min members: {result['min_members']}")
    print(f"databases: {result['database_count']}")
    print(f"tables: {result['table_count']}")
    print(f"columns: {result['column_count']}")
    print(f"databases with table groups: {result['databases_with_groups']}/{result['database_count']}")
    print(f"table groups: {result['group_count']}")
    print(
        "grouped tables: "
        f"{result['grouped_table_count']}/{result['table_count']} "
        f"({_format_pct(result['grouped_table_count'] / result['table_count']) if result['table_count'] else '0.0%'})"
    )
    print(
        "column-set consistency: "
        f"{result['same_column_set_group_count']} same-set groups, "
        f"{result['drifting_group_count']} drifting groups"
    )
    print(f"databases without table groups: {len(result['no_group_databases'])}/{result['database_count']}")

    print("\nTop databases by grouped tables")
    for row in result["databases"][:top_dbs]:
        print(
            f"- {row['db_id']}: groups={row['group_count']}, "
            f"grouped_tables={row['grouped_table_count']}/{row['table_count']} "
            f"({_format_pct(row['grouped_table_ratio'])}), "
            f"same_set={row['same_column_set_group_count']}, drifting={row['drifting_group_count']}, "
            f"columns={row['column_count']}"
        )
        if details:
            for group in row["groups"][:top_groups]:
                print(
                    f"  - {group['schema']}.{group['family']}: "
                    f"members={group['member_count']}, consistency={group['consistency']}, "
                    f"common_cols={group['common_column_count']}, "
                    f"union_cols={group['union_column_count']}, "
                    f"col_counts={group['column_count_distribution']}, "
                    f"examples={group['sample_members']}"
                )

    if not details:
        print("\nTop table groups")
        for group in result["groups"][:top_groups]:
            print(
                f"- {group['db_id']}.{group['schema']}.{group['family']}: "
                f"members={group['member_count']}, consistency={group['consistency']}, "
                f"common_cols={group['common_column_count']}, "
                f"union_cols={group['union_column_count']}, "
                f"examples={group['sample_members']}"
            )

    print("\nLargest databases without table groups")
    for row in result["no_group_databases"][:top_dbs]:
        print(f"- {row['db_id']}: tables={row['table_count']}, columns={row['column_count']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Comma-separated db_id filter.")
    parser.add_argument("--min-members", type=int, default=3)
    parser.add_argument("--top-dbs", type=int, default=25)
    parser.add_argument("--top-groups", type=int, default=25)
    parser.add_argument("--details", action="store_true", help="Print top groups under each top database.")
    parser.add_argument("--json-output", type=Path, help="Write full analysis JSON to this path.")
    args = parser.parse_args()

    db_filter = set(parse_csv_arg(args.db) or [])
    result = analyze_table_groups(db_filter or None, min_members=max(2, args.min_members))
    _print_report(result, top_dbs=args.top_dbs, top_groups=args.top_groups, details=args.details)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON written: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
