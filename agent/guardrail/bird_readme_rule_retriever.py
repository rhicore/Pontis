"""Hybrid retrieval for BIRD README rules.

The retriever only selects candidate README rules for the LLM reviewer. It does
not decide whether a SQL violates a rule, and it must not contain rule-specific
logic, field-specific boosts, database priors, or hand-written rule triggers.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import os
import re
from typing import Iterable

try:
    from utils.embedding import load_embedding_config
except Exception:  # pragma: no cover - optional runtime dependency
    load_embedding_config = None


@dataclass(frozen=True)
class RuleCard:
    rule_id: str
    text: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class _RuleDoc:
    rule_id: str
    text: str
    tokens: frozenset[str]
    code_tokens: frozenset[str]


_TOKEN_RE = re.compile(r"\|\||[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,}")
_COMMON_SQL_CODE_TOKENS = frozenset(
    {
        "select",
        "id",
        "from",
        "where",
        "join",
        "left",
        "inner",
        "on",
        "and",
        "or",
        "not",
        "is",
        "null",
        "in",
        "like",
        "between",
        "group",
        "by",
        "having",
        "order",
        "limit",
        "offset",
        "distinct",
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "case",
        "when",
        "then",
        "else",
        "end",
        "as",
        "cast",
        "with",
    }
)
_SQL_FUNCTION_NAMES = frozenset(
    {
        "abs",
        "avg",
        "cast",
        "coalesce",
        "concat",
        "count",
        "date",
        "format",
        "ifnull",
        "instr",
        "julianday",
        "length",
        "lower",
        "lpad",
        "max",
        "min",
        "nullif",
        "printf",
        "replace",
        "round",
        "strftime",
        "substr",
        "substring",
        "sum",
        "trim",
        "upper",
    }
)


def retrieve_bird_readme_rules(
    bird_readme: str,
    *,
    question: str,
    evidence: str,
    sql: str,
    recent_context: str = "",
    top_k: int = 18,
    project_path: str | None = None,
) -> list[RuleCard]:
    """Return candidate README rules using only current README text signals."""

    docs = _parse_rule_docs(bird_readme)
    if not docs:
        return []

    query = "\n".join(
        part.strip()
        for part in (question, evidence, sql, recent_context)
        if part and part.strip()
    )
    query_tokens = _tokenize(query)
    sql_signal_tokens = _sql_signal_tokens(sql)
    keyword_scores = _keyword_scores(docs, query_tokens, sql_signal_tokens)
    semantic_scores = _semantic_scores(bird_readme, docs, query, project_path)

    cards: list[RuleCard] = []
    for doc in docs:
        score = keyword_scores.get(doc.rule_id, 0.0) + semantic_scores.get(doc.rule_id, 0.0)
        reasons: list[str] = []
        if keyword_scores.get(doc.rule_id, 0.0) > 0:
            reasons.append("keyword")
        if semantic_scores.get(doc.rule_id, 0.0) > 0:
            reasons.append("semantic")
        if score > 0:
            cards.append(RuleCard(doc.rule_id, doc.text, score, tuple(reasons)))

    cards.sort(key=lambda c: (-c.score, _rule_number(c.rule_id)))
    return cards[: max(1, top_k)]


def format_rule_cards(cards: Iterable[RuleCard]) -> str:
    lines = []
    for idx, card in enumerate(cards, 1):
        reasons = f"  retrieval: {', '.join(card.reasons)}" if card.reasons else ""
        lines.append(f"{idx}. {card.text}{reasons}")
    return "\n".join(lines) if lines else "(none)"


@lru_cache(maxsize=16)
def _parse_rule_docs(bird_readme: str) -> tuple[_RuleDoc, ...]:
    docs = []
    for line in bird_readme.splitlines():
        match = re.match(r"^(R\d+)\.\s+(.*)$", line.strip())
        if not match:
            continue
        rid, body = match.groups()
        text = f"{rid}. {body.strip()}"
        docs.append(_RuleDoc(rid, text, frozenset(_tokenize(text)), _code_tokens(text)))
    return tuple(docs)


def _keyword_scores(
    docs: tuple[_RuleDoc, ...],
    query_tokens: frozenset[str],
    sql_signal_tokens: frozenset[str],
) -> dict[str, float]:
    if not query_tokens:
        return {}
    df: dict[str, int] = {}
    for doc in docs:
        for token in doc.tokens:
            df[token] = df.get(token, 0) + 1
    total_docs = len(docs)
    scores = {}
    for doc in docs:
        overlap = doc.tokens & query_tokens
        if not overlap:
            continue
        score = 0.0
        for token in overlap:
            score += math.log((1 + total_docs) / (1 + df.get(token, 0))) + 1.0
        sql_code_overlap = (doc.code_tokens & sql_signal_tokens) - _COMMON_SQL_CODE_TOKENS
        if sql_code_overlap:
            score += 72.0 * len(sql_code_overlap)
        scores[doc.rule_id] = score
    return scores


def _semantic_scores(
    bird_readme: str,
    docs: tuple[_RuleDoc, ...],
    query: str,
    project_path: str | None,
) -> dict[str, float]:
    if not query.strip() or not _embedding_enabled():
        return {}
    vectors = _rule_vectors(bird_readme, tuple(doc.text for doc in docs), project_path)
    if not vectors:
        return {}
    query_vector = _embed_one(query, project_path)
    if not query_vector:
        return {}
    scores = {}
    for doc, vector in zip(docs, vectors):
        sim = _cosine(query_vector, vector)
        if sim > 0:
            scores[doc.rule_id] = sim * 3.0
    return scores


def _embedding_enabled() -> bool:
    value = os.environ.get("PONTIS_BIRD_README_RULE_EMBEDDING", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


@lru_cache(maxsize=8)
def _rule_vectors(bird_readme: str, texts: tuple[str, ...], project_path: str | None) -> tuple[tuple[float, ...], ...]:
    vectors = _embed_many(texts, project_path)
    return tuple(tuple(v) for v in vectors)


def _embed_one(text: str, project_path: str | None) -> list[float]:
    vectors = _embed_many((text,), project_path)
    return vectors[0] if vectors else []


def _embed_many(texts: tuple[str, ...], project_path: str | None) -> list[list[float]]:
    if load_embedding_config is None:
        return []
    try:
        config = load_embedding_config(project_path)
        client = config.get_client()
        if client is None:
            return []
        return client.embed(texts)
    except Exception:
        return []


def _cosine(left: list[float], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _tokenize(text: str) -> frozenset[str]:
    tokens = set()
    for raw in _TOKEN_RE.findall(text.lower()):
        tokens.add(raw)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", raw):
            for i in range(len(raw) - 1):
                tokens.add(raw[i : i + 2])
    return frozenset(tokens)


def _code_tokens(text: str) -> frozenset[str]:
    tokens = set()
    for match in re.finditer(r"`([^`]+)`", text or ""):
        snippet = match.group(1)
        tokens.update(_sql_signal_tokens(snippet))
        for raw in re.findall(r"\b[A-Z][A-Z0-9_]{1,}\b", snippet):
            tokens.add(raw.lower())
        stripped = snippet.strip().lower()
        if stripped in _SQL_FUNCTION_NAMES:
            tokens.add(stripped)
    return frozenset(tokens)


def _sql_signal_tokens(text: str) -> frozenset[str]:
    tokens = set()
    lowered = (text or "").lower()
    if "||" in lowered:
        tokens.add("||")
    if re.search(r"\bcast\s*\([^)]*\bas\s+text\b", lowered):
        tokens.add("cast_as_text")
    for raw in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text or ""):
        token = raw.lower()
        if token in _SQL_FUNCTION_NAMES:
            tokens.add(token)
    return frozenset(tokens)


def _rule_number(rule_id: str) -> int:
    match = re.search(r"\d+", rule_id)
    return int(match.group(0)) if match else 0
