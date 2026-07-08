"""Semantic embedding extractor.

Runs after metadata-generating extractors. It embeds graph nodes with
AI-authored detail and/or official annotations, then stores vectors in Neo4j
for vector search.
"""

from __future__ import annotations

import hashlib
import logging
import re

from storage.workspace import Workspace
from utils.embedding import load_embedding_config

logger = logging.getLogger(__name__)

VECTOR_PROPERTY = "detail_embedding"
VECTOR_INDEX = "pontis_detail_embedding"
VECTOR_INDEX_PREFIX = VECTOR_INDEX
MODEL_PROPERTY = "detail_embedding_model"
HASH_PROPERTY = "detail_embedding_hash"
DIM_PROPERTY = "detail_embedding_dimensions"
_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def generate(workspace: Workspace, config=None) -> None:
    logger.info("=== Semantic embedding: metadata vectors ===")

    embed_config = config.get_embedding_config() if config and hasattr(config, "get_embedding_config") else load_embedding_config()
    client = embed_config.get_client()
    if not client:
        logger.warning("Embedding API not configured, skipping semantic embedding")
        return

    _ensure_node_ids(workspace)
    pending = _pending_nodes(workspace, embed_config.model, embed_config.dimensions)
    if not pending:
        logger.info("  Semantic embedding: already up to date")
        _ensure_vector_indexes(workspace, embed_config.dimensions)
        return

    total = 0
    actual_dimensions = embed_config.dimensions
    batch_size = max(1, embed_config.batch_size)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        texts = [item["text"] for item in batch]
        vectors = client.embed(texts)
        if len(vectors) != len(batch):
            logger.warning("Embedding API returned %s vectors for %s inputs", len(vectors), len(batch))
            continue
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
                "labels": item["labels"],
            })
        if rows:
            actual_dimensions = len(rows[-1]["vector"])
            _write_vectors(workspace, rows)
            total += len(rows)
            logger.info("  Semantic embedding: %s/%s", total, len(pending))

    if total:
        _ensure_vector_indexes(workspace, actual_dimensions)
        logger.info("Semantic embedding done: %s nodes", total)

    if config and hasattr(config, "add_preprocess_token_metrics") and hasattr(client, "metrics"):
        config.add_preprocess_token_metrics(client.metrics())


def _ensure_node_ids(workspace: Workspace) -> None:
    workspace.cypher(
        "MATCH (n) "
        "WHERE "
        "(n.detail IS NOT NULL AND trim(toString(n.detail)) <> '') OR "
        "(n.official_column_description IS NOT NULL AND trim(toString(n.official_column_description)) <> '') OR "
        "(n.official_value_description IS NOT NULL AND trim(toString(n.official_value_description)) <> '') "
        "SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8))"
    )


def _pending_nodes(workspace: Workspace, model: str, dimensions: int) -> list[dict]:
    rows = workspace.cypher(
        "MATCH (n) "
        "WHERE "
        "(n.detail IS NOT NULL AND trim(toString(n.detail)) <> '') OR "
        "(n.official_column_description IS NOT NULL AND trim(toString(n.official_column_description)) <> '') OR "
        "(n.official_value_description IS NOT NULL AND trim(toString(n.official_value_description)) <> '') "
        "RETURN n"
    )
    pending = []
    for row in rows:
        node = row.get("n") or {}
        text = _embedding_text(node)
        node_id = node.get("id")
        if not text or not node_id:
            continue
        labels = _clean_vector_labels(node.get("labels", []))
        if not labels:
            continue
        text_hash = _hash_text(text)
        if (
            node.get(HASH_PROPERTY) == text_hash
            and node.get(MODEL_PROPERTY) == model
            and int(node.get(DIM_PROPERTY) or 0) == int(dimensions or 0)
            and node.get(VECTOR_PROPERTY)
        ):
            continue
        pending.append({
            "id": node_id,
            "text": text,
            "hash": text_hash,
            "labels": labels,
        })
    return pending


def _embedding_text(node: dict) -> str:
    parts = []
    for key in (
        "name",
        "brief",
        "detail",
        "official_column_description",
        "official_value_description",
    ):
        value = str(node.get(key) or "").strip()
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


def _write_vectors(workspace: Workspace, rows: list[dict]) -> None:
    workspace.cypher(
        "UNWIND $rows AS row "
        "MATCH (n {id: row.id}) "
        f"SET n.{VECTOR_PROPERTY} = row.vector, "
        f"n.{MODEL_PROPERTY} = row.model, "
        f"n.{HASH_PROPERTY} = row.hash, "
        f"n.{DIM_PROPERTY} = row.dimensions",
        params={"rows": rows},
    )


def _ensure_vector_indexes(workspace: Workspace, dimensions: int) -> None:
    if not dimensions:
        return
    labels = _embedding_labels(workspace)
    for label in labels:
        _ensure_vector_index(workspace, label, dimensions)


def _ensure_vector_index(workspace: Workspace, label: str, dimensions: int) -> None:
    if not label:
        return
    index_name = vector_index_name(label)
    try:
        workspace.cypher(
            f"CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS "
            f"FOR (n:`{label}`) ON (n.{VECTOR_PROPERTY}) "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {int(dimensions)}, "
            "`vector.similarity_function`: 'cosine'"
            "}}"
        )
        try:
            workspace.cypher(f"CALL db.awaitIndex('{index_name}', 30)")
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Failed to create Neo4j vector index %s: %s", index_name, exc)


def _embedding_labels(workspace: Workspace) -> list[str]:
    rows = workspace.cypher(
        f"MATCH (n) WHERE n.{VECTOR_PROPERTY} IS NOT NULL RETURN DISTINCT labels(n) AS labels"
    )
    labels = set()
    for row in rows:
        labels.update(_clean_vector_labels(row.get("labels") or []))
    return sorted(labels)


def _clean_vector_labels(labels: list[str]) -> list[str]:
    return sorted({
        label
        for label in labels or []
        if isinstance(label, str) and _LABEL_RE.match(label)
    })


def vector_index_name(label: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", label)
    return f"{VECTOR_INDEX_PREFIX}_{safe}"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
