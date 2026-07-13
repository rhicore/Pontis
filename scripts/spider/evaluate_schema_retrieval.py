#!/usr/bin/env python3
"""Evaluate Spider2-Snow table/column retrieval against local Gold SQL."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

PONTIS_ROOT = Path(__file__).resolve().parents[2]
if str(PONTIS_ROOT) not in sys.path:
    sys.path.insert(0, str(PONTIS_ROOT))

from scripts.spider.common import (
    SPIDER2_SNOW_DOCUMENTS,
    SPIDER2_SNOW_GOLD_SQL_DIR,
    get_project_dir,
    load_spider2_snow_cases,
    parse_csv_arg,
)
from scripts.spider.extract_gold_value_overlaps import _load_official_db_metadata
from storage.workspace import Workspace
from tool.utils.entity_search import _bm25_search, _vector_search


DEFAULT_TOP_KS = (5, 10, 20, 50, 100)


def _norm(value: str) -> str:
    return str(value or "").casefold()


def _query_text(case) -> str:
    parts = [f"instruction: {case.instruction.strip()}"]
    if case.external_knowledge:
        path = SPIDER2_SNOW_DOCUMENTS / case.external_knowledge
        if path.exists():
            parts.append(f"external knowledge:\n{path.read_text(encoding='utf-8')[:20000]}")
        else:
            parts.append(f"external knowledge file: {case.external_knowledge}")
    return "\n".join(parts)


def extract_golden_refs(sql: str, db_meta: dict) -> tuple[set[str], list[str]]:
    """Resolve physical table/column refs visible in all SQLGlot scopes."""
    if db_meta.get("status") != "ok":
        return set(), [str(db_meta.get("status") or "metadata_error")]

    table_columns: dict[str, set[str]] = defaultdict(set)
    tables_by_name: dict[str, list[dict]] = defaultdict(list)
    for column in db_meta.get("columns") or []:
        table_ref = str(column.get("table_ref") or column.get("table") or "")
        if not table_ref:
            continue
        table_columns[table_ref].add(_norm(column.get("column")))
        key = _norm(column.get("table_name"))
        if not any(item["ref"] == table_ref for item in tables_by_name[key]):
            tables_by_name[key].append({
                "ref": table_ref,
                "schema": _norm(column.get("schema_name")),
                "name": str(column.get("table_name") or ""),
            })

    roots = sqlglot.parse(sql, read="snowflake")
    refs: set[str] = set()
    unresolved: list[str] = []
    for root in roots:
        if root is None:
            continue
        for scope in traverse_scope(root):
            physical_sources: dict[str, list[dict]] = {}
            for alias, selected in scope.selected_sources.items():
                source = selected[1]
                if not isinstance(source, exp.Table):
                    continue
                resolved = _resolve_table(source, tables_by_name)
                if not resolved:
                    unresolved.append(f"table:{source.sql(dialect='snowflake')}")
                    continue
                physical_sources[_norm(alias)] = resolved
                physical_sources.setdefault(_norm(source.name), resolved)
                refs.update(item["ref"] for item in resolved)

            for column in scope.columns:
                if column.is_star:
                    continue
                candidates: list[dict] = []
                if column.table:
                    if _norm(column.table) not in physical_sources:
                        # The qualifier belongs to a CTE or derived table. Its
                        # physical inputs are collected in that source scope.
                        continue
                    candidates = physical_sources.get(_norm(column.table), [])
                else:
                    if not physical_sources:
                        continue
                    unique = {
                        item["ref"]: item
                        for source_items in physical_sources.values()
                        for item in source_items
                        if _norm(column.name) in table_columns.get(item["ref"], set())
                    }
                    candidates = list(unique.values())
                matching = [
                    item for item in candidates
                    if _norm(column.name) in table_columns.get(item["ref"], set())
                ]
                if len(matching) == 1:
                    refs.add(f"{matching[0]['ref']}--{column.name}")
                elif not matching:
                    unresolved.append(f"column:{column.sql(dialect='snowflake')}")
                else:
                    unresolved.append(f"ambiguous_column:{column.sql(dialect='snowflake')}")
    return refs, sorted(set(unresolved))


def _resolve_table(table: exp.Table, tables_by_name: dict[str, list[dict]]) -> list[dict]:
    matches = list(tables_by_name.get(_norm(table.name), []))
    schema = _norm(table.db)
    if schema:
        matches = [item for item in matches if item["schema"] == schema]
    return matches


def _ranked_refs(workspace: Workspace, text: str, top_k: int) -> tuple[list[str], str]:
    results = _vector_search(workspace, text, "*:table|col", fetch_k=top_k)
    method = "vector"
    if not results:
        results = _bm25_search(workspace, text, "*:table|col")
        method = "bm25"
    refs = []
    seen = set()
    for _score, _name, node, _info, _labels, _project in results:
        ref = str(node.get("_ref") or node.get("path") or "")
        key = _norm(ref)
        if not ref or key in seen:
            continue
        seen.add(key)
        refs.append(ref)
        if len(refs) >= top_k:
            break
    return refs, method


def _ranked_navigation_tables(workspace: Workspace, text: str, top_k: int) -> dict[str, int]:
    results = _vector_search(
        workspace,
        text,
        "*:topic|table_group|table",
        fetch_k=top_k,
    )
    if not results:
        results = _bm25_search(workspace, text, "*:topic|table_group|table")

    table_ranks: dict[str, int] = {}
    navigation_ids: list[dict] = []
    for rank, (_score, _name, node, _info, labels, _project) in enumerate(results[:top_k], start=1):
        label_set = set(labels or node.get("labels") or [])
        ref = str(node.get("_ref") or node.get("path") or "")
        if "table" in label_set and ref:
            key = _norm(ref)
            table_ranks[key] = min(rank, table_ranks.get(key, rank))
        if label_set & {"topic", "table_group"} and node.get("id"):
            navigation_ids.append({"id": node["id"], "rank": rank})

    if navigation_ids:
        rows = workspace.cypher(
            """
            UNWIND $nodes AS selected
            MATCH (n {id: selected.id})
            OPTIONAL MATCH (n)--(direct:table)
            OPTIONAL MATCH (n)--(:table_group)--(member:table)
            WITH selected,
                 collect(DISTINCT coalesce(direct._ref, direct.path))
                 + collect(DISTINCT coalesce(member._ref, member.path)) AS table_refs
            UNWIND table_refs AS table_ref
            WITH selected.rank AS rank, table_ref
            WHERE table_ref IS NOT NULL
            RETURN table_ref, min(rank) AS rank
            """,
            params={"nodes": navigation_ids},
        )
        for row in rows:
            key = _norm(row.get("table_ref"))
            rank = int(row.get("rank") or top_k + 1)
            if key:
                table_ranks[key] = min(rank, table_ranks.get(key, rank))
    return table_ranks


def evaluate(args: argparse.Namespace) -> dict:
    top_ks = tuple(sorted(set(args.top_k)))
    max_k = max(top_ks)
    cases = load_spider2_snow_cases(
        db=args.db,
        instances=parse_csv_arg(args.instances),
        limit=args.limit,
        dev_only=True,
    )
    metadata_cache: dict[str, dict] = {}
    workspaces: dict[str, Workspace] = {}
    results = []
    for case in cases:
        gold_path = SPIDER2_SNOW_GOLD_SQL_DIR / f"{case.instance_id}.sql"
        if not gold_path.exists():
            continue
        db_meta = _load_official_db_metadata(case.db_id, metadata_cache)
        try:
            golden, unresolved = extract_golden_refs(
                gold_path.read_text(encoding="utf-8"),
                db_meta,
            )
        except Exception as exc:
            results.append({
                "instance_id": case.instance_id,
                "db_id": case.db_id,
                "error": f"gold_parse:{type(exc).__name__}:{exc}",
            })
            continue
        if case.db_id not in workspaces:
            workspaces[case.db_id] = Workspace(project_path=str(get_project_dir(case.db_id)))
        workspace = workspaces[case.db_id]
        query_text = _query_text(case)
        try:
            ranked, method = _ranked_refs(workspace, query_text, max_k)
            navigation_table_ranks = _ranked_navigation_tables(
                workspace,
                query_text,
                max_k,
            )
        except Exception as exc:
            results.append({
                "instance_id": case.instance_id,
                "db_id": case.db_id,
                "error": f"retrieval:{type(exc).__name__}:{exc}",
                "golden_count": len(golden),
                "unresolved_gold": unresolved,
            })
            continue
        rank = {_norm(ref): index for index, ref in enumerate(ranked, start=1)}
        golden_rows = [
            {
                "ref": ref,
                "kind": "col" if ref.count("--") >= 3 else "table",
                "rank": rank.get(_norm(ref)),
                "parent_table_ref": "--".join(ref.split("--")[:3]) if ref.count("--") >= 3 else None,
                "navigation_rank": navigation_table_ranks.get(
                    _norm("--".join(ref.split("--")[:3]) if ref.count("--") >= 3 else ref)
                ),
            }
            for ref in sorted(golden)
        ]
        results.append({
            "instance_id": case.instance_id,
            "db_id": case.db_id,
            "method": method,
            "golden_count": len(golden_rows),
            "unresolved_gold": unresolved,
            "golden": golden_rows,
        })

    valid = [row for row in results if not row.get("error") and row.get("golden")]
    summary = {
        "cases_selected": len(cases),
        "cases_evaluated": len(valid),
        "cases_with_errors": sum(bool(row.get("error")) for row in results),
        "cases_with_unresolved_gold": sum(bool(row.get("unresolved_gold")) for row in valid),
        "perfect_recall_at_k": {
            str(k): sum(
                all(item["rank"] is not None and item["rank"] <= k for item in row["golden"])
                for row in valid
            ) / len(valid) if valid else 0.0
            for k in top_ks
        },
        "object_recall_at_k": {
            str(k): (
                sum(item["rank"] is not None and item["rank"] <= k for row in valid for item in row["golden"])
                / sum(len(row["golden"]) for row in valid)
                if valid else 0.0
            )
            for k in top_ks
        },
        "staged_perfect_recall_at_k": {
            str(k): sum(
                all(_staged_hit(item, k, row["golden"]) for item in row["golden"])
                for row in valid
            ) / len(valid) if valid else 0.0
            for k in top_ks
        },
        "staged_object_recall_at_k": {
            str(k): (
                sum(_staged_hit(item, k, row["golden"]) for row in valid for item in row["golden"])
                / sum(len(row["golden"]) for row in valid)
                if valid else 0.0
            )
            for k in top_ks
        },
        "full_recall_rank": _rank_distribution(valid),
    }
    output = {"summary": summary, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _staged_hit(item: dict, top_k: int, golden: list[dict]) -> bool:
    if item["rank"] is not None and item["rank"] <= top_k:
        return True
    if item.get("navigation_rank") is not None and item["navigation_rank"] <= top_k:
        return True
    if item["kind"] != "col" or not item.get("parent_table_ref"):
        return False
    parent = next((candidate for candidate in golden if candidate["ref"] == item["parent_table_ref"]), None)
    return bool(
        parent
        and (
            (parent["rank"] is not None and parent["rank"] <= top_k)
            or (parent.get("navigation_rank") is not None and parent["navigation_rank"] <= top_k)
        )
    )


def _rank_distribution(results: list[dict]) -> dict:
    values = []
    unreachable = 0
    for row in results:
        ranks = [item["rank"] for item in row["golden"]]
        if any(rank is None for rank in ranks):
            unreachable += 1
        else:
            values.append(max(ranks))
    values.sort()

    def percentile(fraction: float):
        if not values:
            return None
        return values[max(0, math.ceil(len(values) * fraction) - 1)]

    return {
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "max": max(values, default=None),
        "unreachable_cases": unreachable,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Comma-separated database ids")
    parser.add_argument("--instances", help="Comma-separated instance ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--top-k", type=int, action="append", default=list(DEFAULT_TOP_KS))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("workspace/baselines/pontis/analysis/spider2_snow/schema_retrieval.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = evaluate(args)
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
