#!/usr/bin/env python3
"""Report Gold JOIN coverage by unified column domains and confirmed relations."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from agent.guardrail.sql_utils import extract_join_col_pairs
from scripts.BIRD.common import get_data_dir, get_db_dir, iter_db_dirs
from scripts.preprocess_engine import init_workspace
from scripts.spider.common import get_project_dir, load_spider2_snow_cases


ROOT = Path(__file__).resolve().parents[1]
TEXT2SQL_ROOT = ROOT.parent
DEFAULT_SPIDER_GOLD = (
    TEXT2SQL_ROOT / "workspace/baselines/pontis/analysis/spider2_snow/"
    "gold_value_overlap_lineage_after_shape_fix/gold_value_overlap_pairs.csv"
)
DEFAULT_OUTPUT = TEXT2SQL_ROOT / "workspace/baselines/pontis/analysis/column_domain_join_coverage"

Endpoint = tuple[str, str, str]
JoinPair = frozenset[Endpoint]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("bird", "spider"), required=True)
    parser.add_argument("--db", help="Comma-separated database filter")
    parser.add_argument("--spider-gold-pairs", type=Path, default=DEFAULT_SPIDER_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    selected = {item.strip() for item in (args.db or "").split(",") if item.strip()}
    if args.dataset == "bird":
        gold, questions = _bird_gold(selected)
        db_ids = sorted(gold)
        workspace_for = lambda db_id: init_workspace(str(get_db_dir(db_id, False)))[0]
    else:
        gold = _spider_gold(args.spider_gold_pairs, selected)
        questions = {}
        db_ids = sorted(
            selected
            or {case.db_id for case in load_spider2_snow_cases(dev_only=True)}
        )
        for db_id in db_ids:
            gold.setdefault(db_id, set())
        workspace_for = lambda db_id: init_workspace(str(get_project_dir(db_id)))[0]

    rows = []
    for db_id in db_ids:
        workspace = workspace_for(db_id)
        graph = _graph_pairs(workspace)
        db_gold = gold.get(db_id, set())
        row = {
            "db_id": db_id,
            "gold_join_pairs": len(db_gold),
            "domain_count": graph["domain_count"],
            "domain_covered": _covered_count(db_gold, graph["domain_pairs"]),
            "accepted_domain_covered": _covered_count(db_gold, graph["accepted_domain_pairs"]),
            "confirmed_relation_covered": _covered_count(db_gold, graph["confirmed_pairs"]),
            "any_graph_evidence_covered": _covered_count(
                db_gold,
                graph["domain_pairs"] | graph["confirmed_pairs"],
            ),
        }
        for key in (
            "domain_covered", "accepted_domain_covered",
            "confirmed_relation_covered", "any_graph_evidence_covered",
        ):
            row[key + "_rate"] = round(row[key] / len(db_gold), 6) if db_gold else None
        if db_id in questions:
            question_pairs = questions[db_id]
            supported = graph["domain_pairs"] | graph["confirmed_pairs"]
            joined_questions = [pairs for pairs in question_pairs if pairs]
            row["gold_join_questions"] = len(joined_questions)
            row["perfect_question_coverage"] = sum(
                all(_pair_is_covered(pair, supported) for pair in pairs)
                for pairs in joined_questions
            )
            row["perfect_question_coverage_rate"] = (
                round(row["perfect_question_coverage"] / len(joined_questions), 6)
                if joined_questions else None
            )
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    summary = _summary(args.dataset, rows)
    out_dir = args.output / args.dataset
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "per_database": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_markdown(summary, rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(out_dir)
    return 0


def _bird_gold(selected: set[str]) -> tuple[dict[str, set[JoinPair]], dict[str, list[set[JoinPair]]]]:
    rows = json.loads((get_data_dir(False) / "dev.json").read_text(encoding="utf-8"))
    gold: dict[str, set[JoinPair]] = defaultdict(set)
    questions: dict[str, list[set[JoinPair]]] = defaultdict(list)
    for row in rows:
        db_id = str(row["db_id"])
        if selected and db_id not in selected:
            continue
        pairs = {
            _pair(("", t1, c1), ("", t2, c2))
            for t1, c1, t2, c2 in extract_join_col_pairs(str(row.get("SQL") or ""))
        }
        gold[db_id].update(pairs)
        questions[db_id].append(pairs)
    for db_dir in iter_db_dirs(False):
        if not selected or db_dir.name in selected:
            gold.setdefault(db_dir.name, set())
    return dict(gold), dict(questions)


def _spider_gold(path: Path, selected: set[str]) -> dict[str, set[JoinPair]]:
    gold: dict[str, set[JoinPair]] = defaultdict(set)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            db_id = str(row.get("db_id") or "")
            if not db_id or (selected and db_id not in selected):
                continue
            left = _spider_endpoint(db_id, row.get("left_source") or "")
            right = _spider_endpoint(db_id, row.get("right_source") or "")
            if left and right and left != right:
                gold[db_id].add(_pair(left, right))
    return dict(gold)


def _spider_endpoint(db_id: str, ref: str) -> Endpoint | None:
    parts = str(ref).split(".")
    if parts and parts[0].upper() == db_id.upper():
        parts = parts[1:]
    if len(parts) < 3:
        return None
    return _endpoint(parts[-3], parts[-2], parts[-1])


def _graph_pairs(workspace) -> dict:
    domain_rows = workspace.cypher(
        """
        MATCH (d:column_domain)--(m)
        WHERE m:col OR m:logical_col
        OPTIONAL MATCH (m)--(pc:col)
        WITH d, m, collect(DISTINCT pc) AS physical
        UNWIND CASE WHEN m:col THEN [m] ELSE physical END AS c
        MATCH (t)--(c)
        WHERE t:table OR t:view
        RETURN d._ref AS domain_ref,
               coalesce(d.review_status, 'pending_review') AS review_status,
               coalesce(c.schema_name, t.schema_name, '') AS schema_name,
               t.name AS table_name,
               c.name AS column_name
        """
    )
    endpoints_by_domain: dict[str, set[Endpoint]] = defaultdict(set)
    status_by_domain: dict[str, str] = {}
    for row in domain_rows:
        ref = str(row.get("domain_ref") or "")
        endpoint = _endpoint(row.get("schema_name"), row.get("table_name"), row.get("column_name"))
        if ref and endpoint[1] and endpoint[2]:
            endpoints_by_domain[ref].add(endpoint)
            status_by_domain[ref] = str(row.get("review_status") or "pending_review")

    domain_pairs: set[JoinPair] = set()
    accepted_pairs: set[JoinPair] = set()
    for ref, endpoints in endpoints_by_domain.items():
        pairs = {_pair(left, right) for left, right in combinations(sorted(endpoints), 2)}
        domain_pairs.update(pairs)
        if status_by_domain.get(ref) == "accepted":
            accepted_pairs.update(pairs)

    relation_rows = workspace.cypher(
        """
        MATCH (r)--(c:col)--(t)
        WHERE (r:fk OR r:rel) AND (t:table OR t:view)
        RETURN coalesce(r._ref, r.name) AS relation_ref,
               coalesce(c.schema_name, t.schema_name, '') AS schema_name,
               t.name AS table_name,
               c.name AS column_name
        """
    )
    endpoints_by_relation: dict[str, set[Endpoint]] = defaultdict(set)
    for row in relation_rows:
        endpoints_by_relation[str(row.get("relation_ref") or "")].add(
            _endpoint(row.get("schema_name"), row.get("table_name"), row.get("column_name"))
        )
    confirmed = {
        _pair(left, right)
        for endpoints in endpoints_by_relation.values()
        for left, right in combinations(sorted(endpoints), 2)
    }
    return {
        "domain_count": len(endpoints_by_domain),
        "domain_pairs": domain_pairs,
        "accepted_domain_pairs": accepted_pairs,
        "confirmed_pairs": confirmed,
    }


def _endpoint(schema, table, column) -> Endpoint:
    return (
        str(schema or "").strip('"`').lower(),
        str(table or "").strip('"`').lower(),
        str(column or "").strip('"`').lower(),
    )


def _pair(left: Endpoint, right: Endpoint) -> JoinPair:
    return frozenset((_endpoint(*left), _endpoint(*right)))


def _pair_is_covered(gold: JoinPair, candidates: set[JoinPair]) -> bool:
    if gold in candidates:
        return True
    gold_no_schema = frozenset(("", table, column) for _schema, table, column in gold)
    return any(
        frozenset(("", table, column) for _schema, table, column in candidate) == gold_no_schema
        for candidate in candidates
    )


def _covered_count(gold: set[JoinPair], candidates: set[JoinPair]) -> int:
    return sum(_pair_is_covered(pair, candidates) for pair in gold)


def _summary(dataset: str, rows: list[dict]) -> dict:
    total = sum(row["gold_join_pairs"] for row in rows)
    summary = {
        "dataset": dataset,
        "database_count": len(rows),
        "databases_with_gold_join_pairs": sum(bool(row["gold_join_pairs"]) for row in rows),
        "gold_join_pairs": total,
        "domain_count": sum(row["domain_count"] for row in rows),
    }
    for key in (
        "domain_covered", "accepted_domain_covered",
        "confirmed_relation_covered", "any_graph_evidence_covered",
    ):
        value = sum(row[key] for row in rows)
        summary[key] = value
        summary[key + "_rate"] = round(value / total, 6) if total else None
    if dataset == "bird":
        questions = sum(row.get("gold_join_questions", 0) for row in rows)
        perfect = sum(row.get("perfect_question_coverage", 0) for row in rows)
        summary["gold_join_questions"] = questions
        summary["perfect_question_coverage"] = perfect
        summary["perfect_question_coverage_rate"] = round(perfect / questions, 6) if questions else None
    return summary


def _markdown(summary: dict, rows: list[dict]) -> str:
    lines = [
        f"# {summary['dataset'].upper()} Column-Domain Gold JOIN Coverage", "",
        f"- Databases: {summary['database_count']}",
        f"- Databases with Gold JOIN pairs: {summary['databases_with_gold_join_pairs']}",
        f"- Gold JOIN pairs: {summary['gold_join_pairs']}",
        f"- Domains: {summary['domain_count']}",
        f"- Raw domain coverage: {summary['domain_covered']} / {summary['gold_join_pairs']} ({summary['domain_covered_rate']})",
        f"- Accepted-domain coverage: {summary['accepted_domain_covered']} / {summary['gold_join_pairs']} ({summary['accepted_domain_covered_rate']})",
        f"- FK/rel coverage: {summary['confirmed_relation_covered']} / {summary['gold_join_pairs']} ({summary['confirmed_relation_covered_rate']})",
        f"- Any graph evidence: {summary['any_graph_evidence_covered']} / {summary['gold_join_pairs']} ({summary['any_graph_evidence_covered_rate']})",
        "", "## Per database", "",
        "| Database | Domains | Gold pairs | Domain | Accepted domain | FK/rel | Any evidence |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['db_id']}` | {row['domain_count']} | {row['gold_join_pairs']} | "
            f"{row['domain_covered']} | {row['accepted_domain_covered']} | "
            f"{row['confirmed_relation_covered']} | {row['any_graph_evidence_covered']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
