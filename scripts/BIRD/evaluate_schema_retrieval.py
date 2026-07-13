"""Evaluate table/column retrieval recall on BIRD golden SQL.

The query text is ``question + evidence``.  Its embedding is compared with
the existing ``detail_embedding`` vectors of table and column nodes in the
sample's own database.  Golden objects are physical tables and columns
referenced by the golden SQL.

Example:
    uv run python -m scripts.BIRD.evaluate_schema_retrieval
    uv run python -m scripts.BIRD.evaluate_schema_retrieval --db california_schools
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from scripts.BIRD.common import PONTIS_WORKSPACE_ROOT, get_data_dir
from storage.workspace import Workspace
from utils.embedding import load_embedding_config


DEFAULT_TOP_KS = (1, 3, 5, 10, 20, 30, 50, 100)


def _norm(value: str) -> str:
    return str(value or "").casefold()


def _object_id(kind: str, table: str, column: str = "") -> str:
    if kind == "table":
        return f"table:{table}"
    return f"col:{table}.{column}"


def _query_text(sample: dict, include_evidence: bool = True) -> str:
    parts = [f"question: {str(sample.get('question') or '').strip()}"]
    evidence = str(sample.get("evidence") or "").strip()
    if include_evidence and evidence:
        parts.append(f"evidence: {evidence}")
    return "\n".join(parts)


def _load_sqlite_schema(db_dir: Path, db_id: str) -> tuple[dict, dict, dict]:
    db_path = db_dir / f"{db_id}.sqlite"
    if not db_path.exists():
        sqlite_files = sorted(db_dir.glob("*.sqlite"))
        if len(sqlite_files) != 1:
            raise FileNotFoundError(f"Cannot identify SQLite file under {db_dir}")
        db_path = sqlite_files[0]

    schema: dict[str, dict[str, str]] = {}
    table_names: dict[str, str] = {}
    column_names: dict[str, dict[str, str]] = {}
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table_name,) in rows:
            escaped = table_name.replace('"', '""')
            columns = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
            schema[table_name] = {
                row[1]: (row[2] or "UNKNOWN")
                for row in columns
            }
            table_names[_norm(table_name)] = table_name
            column_names[table_name] = {_norm(row[1]): row[1] for row in columns}
    return schema, table_names, column_names


def _physical_source(scope: Scope, alias: str, table_names: dict[str, str]) -> str | None:
    current: Scope | None = scope
    alias_key = _norm(alias)
    while current is not None:
        selected = current.selected_sources.get(alias) or current.selected_sources.get(alias_key)
        if selected:
            _, source = selected
            if isinstance(source, exp.Table):
                return table_names.get(_norm(source.name))
            return None
        current = current.parent
    return None


def extract_golden_objects(
    sql: str,
    schema: dict,
    table_names: dict[str, str],
    column_names: dict[str, dict[str, str]],
) -> set[str]:
    """Return canonical physical table/column objects referenced by SQL."""
    expression = sqlglot.parse_one(sql, read="sqlite")
    expression = qualify(
        expression,
        dialect="sqlite",
        schema=schema,
        expand_stars=False,
        validate_qualify_columns=True,
        quote_identifiers=False,
        identify=False,
    )

    objects: set[str] = set()
    for table in expression.find_all(exp.Table):
        physical_name = table_names.get(_norm(table.name))
        if physical_name:
            objects.add(_object_id("table", physical_name))

    for scope in traverse_scope(expression):
        for column in scope.columns:
            if column.is_star or not column.table:
                continue
            physical_table = _physical_source(scope, column.table, table_names)
            if not physical_table:
                continue
            physical_column = column_names[physical_table].get(_norm(column.name))
            if physical_column:
                objects.add(_object_id("col", physical_table, physical_column))
    return objects


def _load_candidates(
    db_dir: Path,
    db_id: str,
    *,
    columns_only: bool = False,
) -> tuple[list[dict], np.ndarray]:
    workspace = Workspace(project_path=str(db_dir))
    store = workspace._get_store()
    if store is None:
        raise RuntimeError(f"No graph store configured for {db_id}")
    rows = store.execute_cypher(
        "MATCH (n {project: $project}) "
        "WHERE n.detail_embedding IS NOT NULL "
        "AND (($columns_only AND n:col) OR (NOT $columns_only AND (n:table OR n:col))) "
        "RETURN CASE WHEN n:table THEN 'table' ELSE 'col' END AS kind, "
        "n.table_name AS table_name, n.column_name AS column_name, "
        "n.name AS name, n._ref AS node_ref, "
        "n.detail_embedding AS embedding, "
        "n.detail_embedding_model AS model, "
        "n.detail_embedding_dimensions AS dimensions",
        params={"project": db_id, "columns_only": columns_only},
    )
    candidates: list[dict] = []
    vectors: list[list[float]] = []
    seen: set[str] = set()
    for row in rows:
        kind = row["kind"]
        table_name = str(row.get("table_name") or (row.get("name") if kind == "table" else ""))
        column_name = str(row.get("column_name") or (row.get("name") if kind == "col" else ""))
        if not table_name or (kind == "col" and not column_name):
            continue
        object_id = _object_id(kind, table_name, column_name)
        normalized_id = _norm(object_id)
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        candidates.append({
            "id": object_id,
            "normalized_id": normalized_id,
            "kind": kind,
            "table": table_name,
            "column": column_name or None,
            "ref": row.get("node_ref"),
            "model": row.get("model"),
            "dimensions": int(row.get("dimensions") or 0),
        })
        vectors.append(row["embedding"])
    if not vectors:
        raise RuntimeError(f"No embedded table/column candidates in {db_id}")
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    return candidates, matrix


def _cache_key(text: str, model: str, dimensions: int) -> str:
    payload = f"{model}\0{dimensions}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_embedding_cache(path: Path) -> dict[str, list[float]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _save_embedding_cache(path: Path, cache: dict[str, list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle)
    temporary.replace(path)


def _embed_queries(
    texts: list[str],
    *,
    cache_path: Path | None,
    batch_size: int,
) -> tuple[list[list[float]], dict]:
    config = load_embedding_config()
    client = config.get_client()
    if client is None:
        raise RuntimeError("Embedding API is not configured")
    effective_batch_size = max(1, batch_size or config.batch_size)
    cache = _load_embedding_cache(cache_path) if cache_path else {}
    keys = [_cache_key(text, config.model, config.dimensions) for text in texts]
    missing_indices = [index for index, key in enumerate(keys) if key not in cache]
    for start in range(0, len(missing_indices), effective_batch_size):
        indices = missing_indices[start:start + effective_batch_size]
        vectors = client.embed([texts[index] for index in indices])
        if len(vectors) != len(indices):
            raise RuntimeError(
                f"Embedding API returned {len(vectors)} vectors for {len(indices)} queries"
            )
        for index, vector in zip(indices, vectors):
            cache[keys[index]] = vector
        if cache_path:
            _save_embedding_cache(cache_path, cache)
    return [cache[key] for key in keys], {
        "model": config.model,
        "dimensions": config.dimensions,
        "cache_hits": len(texts) - len(missing_indices),
        "cache_misses": len(missing_indices),
        "api_metrics": client.metrics(),
    }


def _perfect_recall_at_n(results: list[dict], top_ks: Iterable[int]) -> dict:
    output = {}
    for top_n in top_ks:
        fully_covered = 0
        for result in results:
            ranks = [item["rank"] for item in result["golden"]]
            fully_covered += int(
                bool(ranks)
                and all(rank is not None and rank <= top_n for rank in ranks)
            )
        output[str(top_n)] = fully_covered / len(results) if results else 1.0
    return output


def _minimum_perfect_top_n(results: list[dict], kind: str | None = None) -> int | None:
    required = []
    for result in results:
        ranks = [
            item["rank"] for item in result["golden"]
            if kind is None or item["kind"] == kind
        ]
        if not ranks:
            continue
        if any(rank is None for rank in ranks):
            return None
        required.append(max(ranks))
    return max(required, default=0)


def _full_recall_rank_distribution(results: list[dict], kind: str | None = None) -> dict:
    required = []
    unreachable = 0
    for result in results:
        ranks = [
            item["rank"] for item in result["golden"]
            if kind is None or item["kind"] == kind
        ]
        if not ranks:
            continue
        if any(rank is None for rank in ranks):
            unreachable += 1
            continue
        required.append(max(ranks))
    required.sort()

    def percentile(fraction: float) -> int | None:
        if not required:
            return None
        index = max(0, math.ceil(len(required) * fraction) - 1)
        return required[index]

    return {
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(required, default=None),
        "unreachable_questions": unreachable,
    }


def evaluate(args: argparse.Namespace) -> dict:
    data_dir = get_data_dir(train=False)
    db_base = data_dir / "dev_databases"
    with (data_dir / "dev.json").open("r", encoding="utf-8") as handle:
        samples = json.load(handle)
    if args.db:
        selected = set(args.db)
        unknown = selected - {sample["db_id"] for sample in samples}
        if unknown:
            raise ValueError(f"Unknown databases: {', '.join(sorted(unknown))}")
        samples = [sample for sample in samples if sample["db_id"] in selected]
    if args.limit is not None:
        samples = samples[:args.limit]
    if not samples:
        raise ValueError("No BIRD samples selected")

    database_data = {}
    for db_id in sorted({sample["db_id"] for sample in samples}):
        db_dir = db_base / db_id
        schema, tables, columns = _load_sqlite_schema(db_dir, db_id)
        candidates, matrix = _load_candidates(
            db_dir,
            db_id,
            columns_only=args.columns_only,
        )
        database_data[db_id] = {
            "schema": schema,
            "tables": tables,
            "columns": columns,
            "candidates": candidates,
            "matrix": matrix,
        }

    texts = [_query_text(sample, not args.no_evidence) for sample in samples]
    vectors, embedding_info = _embed_queries(
        texts,
        cache_path=None if args.no_cache else args.cache,
        batch_size=args.embedding_batch_size,
    )
    for db_id, data in database_data.items():
        graph_models = {item["model"] for item in data["candidates"] if item["model"]}
        graph_dimensions = {
            item["dimensions"] for item in data["candidates"] if item["dimensions"]
        }
        if graph_models != {embedding_info["model"]}:
            raise RuntimeError(
                f"Embedding model mismatch for {db_id}: query={embedding_info['model']}, "
                f"graph={sorted(graph_models)}"
            )
        if graph_dimensions != {embedding_info["dimensions"]}:
            raise RuntimeError(
                f"Embedding dimension mismatch for {db_id}: "
                f"query={embedding_info['dimensions']}, graph={sorted(graph_dimensions)}"
            )

    results = []
    requested_max = max(args.top_k)
    for sample, text, raw_vector in zip(samples, texts, vectors):
        db_id = sample["db_id"]
        data = database_data[db_id]
        vector = np.asarray(raw_vector, dtype=np.float32)
        vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        if vector.shape[0] != data["matrix"].shape[1]:
            raise RuntimeError(
                f"Embedding dimension mismatch for {db_id}: query={vector.shape[0]}, "
                f"graph={data['matrix'].shape[1]}"
            )
        scores = data["matrix"] @ vector
        order = np.argsort(-scores, kind="stable")
        rank_by_id = {
            data["candidates"][candidate_index]["normalized_id"]: rank
            for rank, candidate_index in enumerate(order, start=1)
        }
        golden_ids = extract_golden_objects(
            sample["SQL"], data["schema"], data["tables"], data["columns"]
        )
        if args.columns_only:
            golden_ids = {item for item in golden_ids if item.startswith("col:")}
        golden = []
        for object_id in sorted(golden_ids):
            kind = "table" if object_id.startswith("table:") else "col"
            golden.append({
                "id": object_id,
                "kind": kind,
                "rank": rank_by_id.get(_norm(object_id)),
            })
        top_results = []
        for candidate_index in order[:requested_max]:
            candidate = data["candidates"][candidate_index]
            top_results.append({
                "id": candidate["id"],
                "kind": candidate["kind"],
                "score": float(scores[candidate_index]),
            })
        results.append({
            "question_id": sample.get("question_id"),
            "db_id": db_id,
            "difficulty": sample.get("difficulty"),
            "query_text": text,
            "sql": sample["SQL"],
            "golden": golden,
            "minimum_full_recall_top_n": (
                max((item["rank"] for item in golden), default=0)
                if all(item["rank"] is not None for item in golden)
                else None
            ),
            "top_results": top_results,
        })

    unreachable = [
        {
            "question_id": result["question_id"],
            "db_id": result["db_id"],
            "objects": [item["id"] for item in result["golden"] if item["rank"] is None],
        }
        for result in results
        if any(item["rank"] is None for item in result["golden"])
    ]
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "split": "bird_dev",
        "query": "question + evidence" if not args.no_evidence else "question only",
        "retrieval_scope": "columns_only" if args.columns_only else "tables_and_columns",
        "questions": len(results),
        "databases": sorted(database_data),
        "top_ks": args.top_k,
        "embedding": embedding_info,
        "candidate_counts": {
            db_id: {
                "all": len(data["candidates"]),
                "tables": sum(item["kind"] == "table" for item in data["candidates"]),
                "columns": sum(item["kind"] == "col" for item in data["candidates"]),
            }
            for db_id, data in database_data.items()
        },
        "minimum_top_n_for_100pct_perfect_recall": _minimum_perfect_top_n(results),
        "perfect_recall_rank_distribution": _full_recall_rank_distribution(results),
        "unreachable_questions": unreachable,
        "perfect_recall_at_n": _perfect_recall_at_n(results, args.top_k),
        "per_database": {
            db_id: {
                "questions": len(db_results),
                "minimum_top_n_for_100pct_perfect_recall": _minimum_perfect_top_n(db_results),
                "perfect_recall_rank_distribution": _full_recall_rank_distribution(db_results),
                "perfect_recall_at_n": _perfect_recall_at_n(db_results, args.top_k),
            }
            for db_id in sorted(database_data)
            for db_results in [[result for result in results if result["db_id"] == db_id]]
        },
        "per_question": results,
    }
    return report


def _parse_top_ks(value: str) -> list[int]:
    values = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("top-k values must be positive integers")
    return values


def parse_args() -> argparse.Namespace:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="append", help="Evaluate one database; repeatable")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N selected samples")
    parser.add_argument(
        "--top-k",
        type=_parse_top_ks,
        default=list(DEFAULT_TOP_KS),
        help="Comma-separated Top-N values (default: 1,3,5,10,20,30,50,100)",
    )
    parser.add_argument("--no-evidence", action="store_true", help="Embed question only")
    parser.add_argument(
        "--columns-only",
        action="store_true",
        help="Retrieve only columns and evaluate only golden columns",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=0,
        help="Query embedding batch size; 0 uses the Pontis embedding configuration",
    )
    parser.add_argument("--no-cache", action="store_true", help="Do not cache query embeddings")
    parser.add_argument(
        "--cache",
        type=Path,
        default=PONTIS_WORKSPACE_ROOT / "cache" / "bird_dev_query_embeddings.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PONTIS_WORKSPACE_ROOT / "results" / f"bird_dev_schema_recall_{timestamp}.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    perfect = report["minimum_top_n_for_100pct_perfect_recall"]
    print(f"Questions: {report['questions']}")
    print(f"Unreachable questions: {len(report['unreachable_questions'])}")
    print(f"Minimum Top-N for 100% perfect recall: {perfect}")
    distribution = report["perfect_recall_rank_distribution"]
    print(
        "Per-question full-recall rank: "
        f"p50={distribution['p50']}, p75={distribution['p75']}, "
        f"p90={distribution['p90']}, p95={distribution['p95']}, "
        f"p99={distribution['p99']}, max={distribution['max']}"
    )
    print("Top-N | Perfect Recall")
    for top_n in report["top_ks"]:
        rate = report["perfect_recall_at_n"][str(top_n)]
        print(f"{top_n:>5} | {rate:.4f}")
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
