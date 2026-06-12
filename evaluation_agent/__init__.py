"""External evaluation agents for dataset-level benchmark adaptation."""

from .bird_agent import BirdEvaluationAgent
from .models import BirdCase, CandidateReport, EvaluationResult

__all__ = [
    "BirdEvaluationAgent",
    "BirdCase",
    "CandidateReport",
    "EvaluationResult",
]
