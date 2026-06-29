"""Typed data containers for BIRD benchmark runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BirdCase:
    """One BIRD benchmark question."""

    db_id: str
    question_id: int
    question: str
    evidence: str
    golden_sql: str | None = None
    difficulty: str = "?"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "BirdCase":
        return cls(
            db_id=str(row["db_id"]),
            question_id=int(row.get("question_id", 0)),
            question=str(row["question"]),
            evidence=str(row.get("evidence") or ""),
            golden_sql=row.get("SQL"),
            difficulty=str(row.get("difficulty") or "?"),
        )


@dataclass
class CandidateReport:
    """One Pontis SQL attempt."""

    attempt: int
    action: str
    request: str
    raw_response: str
    predicted_sql: str | None
    elapsed: float
    efficiency: dict[str, Any] = field(default_factory=dict)


@dataclass
class BirdRunResult:
    """Final result for one BIRD case."""

    case: BirdCase
    candidate: CandidateReport
    result: str
    correct: bool
    predicted_execution: Any
    golden_execution: Any = None
    attempts: list[CandidateReport] = field(default_factory=list)
    strict_correct: bool = False
    business_correct: bool = False
    match_type: str = "not_compared"
