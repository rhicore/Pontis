#!/usr/bin/env python3
"""同步 BIRD 全局知识库。

默认行为：
- 将 example_data/bird_train/train.json 的每个 query 导入为
  bird::q<N>:knowledge:example
- 为 train query example 的 detail 生成语义向量

所有图读写都通过 storage.Workspace.cypher(project="bird") 完成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.workspace import Workspace
from extractor.modules.semantic_embedding import (
    DIM_PROPERTY,
    HASH_PROPERTY,
    MODEL_PROPERTY,
    VECTOR_PROPERTY,
    vector_index_name,
)
from utils.embedding import load_embedding_config


TRAIN_JSON_CANDIDATES = [
    TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis" / "data" / "bird_train" / "train.json",
    TEXT2SQL_ROOT / "workspace" / "original_data" / "bird_train" / "train.json",
    PROJECT_ROOT / "example_data" / "bird_train" / "train.json",
    TEXT2SQL_ROOT / "example_data" / "bird_train" / "train.json",
]
TRAIN_JSON_PATH = TRAIN_JSON_CANDIDATES[0]
TRAIN_IMPORT_BATCH_SIZE = 500


def resolve_train_json_path(train_json: Path | None = None) -> Path:
    """Resolve BIRD train.json from the current Text2SQL workspace layout."""
    if train_json:
        return Path(train_json)
    for candidate in TRAIN_JSON_CANDIDATES:
        if candidate.exists():
            return candidate
    return TRAIN_JSON_PATH


def count_bird_train_examples(ws: Workspace | None = None) -> int:
    """Return imported bird_train example count in the global bird graph."""
    own_ws = ws is None
    if ws is None:
        ws = Workspace(active_projects=["bird"])
    try:
        rows = ws.cypher(
            "MATCH (n:knowledge:example {source: 'bird_train'}) RETURN count(n) AS count",
            project="bird",
        )
        return int(rows[0].get("count") or 0) if rows else 0
    finally:
        if own_ws:
            close = getattr(ws, "close", None)
            if callable(close):
                close()


def import_train_examples(ws: Workspace, train_json: Path | None = None) -> int:
    """将 BIRD train query/golden SQL 导入 bird 全局知识图谱。"""
    train_json = resolve_train_json_path(train_json)
    if not train_json.exists():
        raise FileNotFoundError(f"train json not found: {train_json}")

    data = json.loads(train_json.read_text(encoding="utf-8"))
    rows = []
    for idx, item in enumerate(data, start=1):
        qid = item.get("question_id")
        if qid is None:
            qid = idx
        question = str(item.get("question") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        sql = str(item.get("SQL") or item.get("sql") or "").strip()
        db_id = str(item.get("db_id") or "").strip()
        difficulty = str(item.get("difficulty") or "").strip()
        name = f"q{qid}"
        detail = _format_train_detail(
            qid=qid,
            db_id=db_id,
            question=question,
            evidence=evidence,
            sql=sql,
            difficulty=difficulty,
        )
        rows.append({
            "name": name,
            "labels": ["knowledge", "example"],
            "brief": question,
            "detail": detail,
            "question": question,
            "evidence": evidence,
            "golden_sql": sql,
            "db_id": db_id,
            "question_id": qid,
            "difficulty": difficulty,
            "source": "bird_train",
        })

    if not rows:
        return 0

    for offset in range(0, len(rows), TRAIN_IMPORT_BATCH_SIZE):
        batch = rows[offset:offset + TRAIN_IMPORT_BATCH_SIZE]
        ws.cypher(
            "UNWIND $rows AS row "
            "MERGE (n:knowledge:example {name: row.name}) "
            "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
            "ON MATCH SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)) "
            "SET n += row "
            "WITH n, row.labels AS labels "
            "SET n.labels = reduce(acc = [], label IN coalesce(n.labels, []) + labels | "
            "CASE WHEN label IN acc THEN acc ELSE acc + label END) "
            "SET n:knowledge:example",
            params={"rows": batch},
            project="bird",
        )
        print(
            f"Imported BIRD train examples: {min(offset + len(batch), len(rows))}/{len(rows)}",
            flush=True,
        )
    return len(rows)


def embed_train_examples(
    ws: Workspace,
    workers: int | None = None,
    batch_size: int | None = None,
    retries: int = 8,
    retry_base_seconds: float = 2.0,
) -> int:
    """为 bird_train query example 节点生成 detail 语义向量。"""
    embed_config = load_embedding_config()
    client = embed_config.get_client()
    if not client:
        print("Embedding API is not configured; skipped BIRD train example embeddings", flush=True)
        return 0

    pending = _pending_train_examples(ws, embed_config.model, embed_config.dimensions)
    if not pending:
        _ensure_vector_index(ws, embed_config.dimensions)
        print("BIRD train example embeddings are already up to date", flush=True)
        return 0

    actual_dimensions = embed_config.dimensions
    batch_size = max(1, int(batch_size or embed_config.batch_size))
    workers = max(1, int(workers or 1))
    batches = [
        pending[offset:offset + batch_size]
        for offset in range(0, len(pending), batch_size)
    ]

    def embed_batch(batch: list[dict]) -> list[dict]:
        last_exc: Exception | None = None
        for attempt in range(max(0, retries) + 1):
            try:
                vectors = client.embed([item["detail"] for item in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError(
                        f"Embedding API returned {len(vectors)} vectors for {len(batch)} inputs"
                    )
                break
            except Exception as exc:
                last_exc = exc
                message = str(exc)
                retryable = (
                    "429" in message
                    or "limit_requests" in message
                    or "rate" in message.lower()
                    or "timeout" in message.lower()
                )
                if not retryable or attempt >= retries:
                    raise
                sleep_s = retry_base_seconds * (2 ** min(attempt, 4)) + random.uniform(0, 1.5)
                time.sleep(sleep_s)
        else:
            raise last_exc or RuntimeError("Embedding failed")

        rows = []
        for item, vector in zip(batch, vectors):
            if not vector:
                continue
            rows.append({
                "id": item["id"],
                "vector": vector,
                "model": embed_config.model,
                "hash": item["hash"],
                "dimensions": len(vector),
            })
        return rows

    total = 0
    failed = 0
    if workers == 1:
        for batch in batches:
            rows = embed_batch(batch)
            if not rows:
                continue
            _write_train_vectors(ws, rows)
            actual_dimensions = rows[-1]["dimensions"]
            total += len(rows)
            print(f"Embedded BIRD train examples: {total}/{len(pending)}", flush=True)
    else:
        print(
            f"Embedding BIRD train examples with {workers} workers, "
            f"batch_size={batch_size}, batches={len(batches)}",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(embed_batch, batch): idx
                for idx, batch in enumerate(batches, start=1)
            }
            for future in as_completed(futures):
                batch_no = futures[future]
                try:
                    rows = future.result()
                except Exception as exc:
                    failed += 1
                    print(f"Embedding batch {batch_no}/{len(batches)} failed: {exc}", flush=True)
                    continue
                if not rows:
                    continue
                _write_train_vectors(ws, rows)
                actual_dimensions = rows[-1]["dimensions"]
                total += len(rows)
                print(f"Embedded BIRD train examples: {total}/{len(pending)}", flush=True)

    if failed:
        print(f"Embedding finished with {failed} failed batches; rerun to fill pending vectors", flush=True)

    if total:
        _ensure_vector_index(ws, actual_dimensions)
    return total


def _pending_train_examples(ws: Workspace, model: str, dimensions: int) -> list[dict]:
    rows = ws.cypher(
        "MATCH (n:knowledge:example {source: 'bird_train'}) "
        "WHERE n.detail IS NOT NULL AND trim(toString(n.detail)) <> '' "
        f"RETURN n.id AS id, n.detail AS detail, "
        f"n.{HASH_PROPERTY} AS hash, "
        f"n.{MODEL_PROPERTY} AS model, "
        f"n.{DIM_PROPERTY} AS dimensions, "
        f"n.{VECTOR_PROPERTY} IS NOT NULL AS has_vector",
        project="bird",
    )
    pending = []
    for row in rows:
        detail = str(row.get("detail") or "").strip()
        node_id = row.get("id")
        if not detail or not node_id:
            continue
        text_hash = _hash_text(detail)
        if (
            row.get("hash") == text_hash
            and row.get("model") == model
            and int(row.get("dimensions") or 0) == int(dimensions or 0)
            and row.get("has_vector")
        ):
            continue
        pending.append({
            "id": node_id,
            "detail": detail,
            "hash": text_hash,
        })
    return pending


def _write_train_vectors(ws: Workspace, rows: list[dict]) -> None:
    ws.cypher(
        "UNWIND $rows AS row "
        "MATCH (n:knowledge:example {id: row.id}) "
        f"SET n.{VECTOR_PROPERTY} = row.vector, "
        f"n.{MODEL_PROPERTY} = row.model, "
        f"n.{HASH_PROPERTY} = row.hash, "
        f"n.{DIM_PROPERTY} = row.dimensions",
        params={"rows": rows},
        project="bird",
    )


def _ensure_vector_index(ws: Workspace, dimensions: int) -> None:
    if not dimensions:
        return
    index_name = vector_index_name("knowledge")
    try:
        ws.cypher(
            f"CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS "
            f"FOR (n:knowledge) ON (n.{VECTOR_PROPERTY}) "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {int(dimensions)}, "
            "`vector.similarity_function`: 'cosine'"
            "}}",
            project="bird",
        )
        try:
            ws.cypher(f"CALL db.awaitIndex('{index_name}', 30)", project="bird")
        except Exception:
            pass
    except Exception as exc:
        print(f"Failed to create Neo4j vector index {index_name}: {exc}", flush=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _format_train_detail(
    *,
    qid: int,
    db_id: str,
    question: str,
    evidence: str,
    sql: str,
    difficulty: str = "",
) -> str:
    lines = [
        f"Question ID: {qid}",
        f"Database: {db_id or '(unknown)'}",
    ]
    if difficulty:
        lines.append(f"Difficulty: {difficulty}")
    lines.extend([
        "",
        "Question:",
        question or "(empty)",
        "",
        "Evidence:",
        evidence or "(none)",
        "",
        "Golden SQL:",
        "```sql",
        sql or "",
        "```",
    ])
    return "\n".join(lines).strip()


def sync_bird_global(
    import_train: bool = True,
    embed_train: bool = True,
    train_json: Path | None = None,
    embedding_workers: int | None = None,
    embedding_batch_size: int | None = None,
    embedding_retries: int = 8,
    embedding_retry_base_seconds: float = 2.0,
) -> None:
    train_json = resolve_train_json_path(train_json)
    ws = Workspace(active_projects=["bird"])
    if import_train:
        count = import_train_examples(ws, train_json=train_json)
        print(f"Imported {count} BIRD train examples into bird graph", flush=True)
    if embed_train:
        embedded = embed_train_examples(
            ws,
            workers=embedding_workers,
            batch_size=embedding_batch_size,
            retries=embedding_retries,
            retry_base_seconds=embedding_retry_base_seconds,
        )
        print(f"Embedded {embedded} BIRD train examples", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-train", action="store_true", help="不导入 train examples")
    parser.add_argument("--no-embedding", action="store_true", help="导入 train examples 后不生成语义向量")
    parser.add_argument("--train-json", type=Path, help="BIRD train.json 路径")
    parser.add_argument(
        "--embedding-workers",
        type=int,
        default=int(os.environ.get("PONTIS_BIRD_EMBEDDING_WORKERS", "1")),
        help="并发 embedding batch 数",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=None,
        help="每个 embedding 请求包含的 example 数；默认使用全局 embedding batch_size",
    )
    parser.add_argument("--embedding-retries", type=int, default=8, help="每个 embedding batch 的重试次数")
    parser.add_argument(
        "--embedding-retry-base-seconds",
        type=float,
        default=2.0,
        help="429/timeout 重试的基础退避秒数",
    )
    args = parser.parse_args()

    sync_bird_global(
        import_train=not args.no_train,
        embed_train=not args.no_embedding,
        train_json=args.train_json,
        embedding_workers=args.embedding_workers,
        embedding_batch_size=args.embedding_batch_size,
        embedding_retries=args.embedding_retries,
        embedding_retry_base_seconds=args.embedding_retry_base_seconds,
    )
    print("Synced bird global graph", flush=True)


if __name__ == "__main__":
    main()
