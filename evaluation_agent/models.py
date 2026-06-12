"""Typed data containers for dataset-level evaluation agents."""

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
    """Pontis output for one attempt."""

    attempt: int
    action: str
    request: str
    raw_response: str
    predicted_sql: str | None
    elapsed: float
    efficiency: dict[str, Any] = field(default_factory=dict)
    exit_plan_requested: bool = False
    exit_plan_request: dict[str, Any] | None = None
    challenge_reports: list[Any] = field(default_factory=list)
    judge_report: Any | None = None


@dataclass
class EvaluationResult:
    """Final result for one externally supervised case."""

    case: BirdCase
    candidate: CandidateReport
    result: str
    correct: bool
    predicted_execution: set | str
    golden_execution: set | str | None = None
    attempts: list[CandidateReport] = field(default_factory=list)
