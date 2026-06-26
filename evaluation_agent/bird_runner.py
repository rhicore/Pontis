"""Runner for externally supervised BIRD cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from scripts.BIRD.common import get_data_dir, get_db_base

from .bird_agent import BirdEvaluationAgent
from .models import BirdCase, EvaluationResult


def assign_question_ids(questions: list[dict]) -> list[dict]:
    normalized = []
    for idx, item in enumerate(questions):
        item = dict(item)
        if item.get("question_id") is None:
            item["question_id"] = idx
        normalized.append(item)
    return normalized


def load_bird_cases(
    *,
    train: bool = False,
    db: str | None = None,
    qids: Iterable[int] | None = None,
    limit: int | None = None,
) -> list[BirdCase]:
    data_dir = get_data_dir(train)
    json_path = data_dir / ("train.json" if train else "dev.json")
    rows = assign_question_ids(json.loads(json_path.read_text(encoding="utf-8")))
    if db:
        db_filter = {item.strip() for item in db.split(",") if item.strip()}
        rows = [row for row in rows if row.get("db_id") in db_filter]
    if qids:
        qid_set = {int(qid) for qid in qids}
        rows = [row for row in rows if int(row.get("question_id", 0)) in qid_set]
    if limit is not None:
        rows = rows[:limit]
    return [BirdCase.from_row(row) for row in rows]


def run_case(
    case: BirdCase,
    *,
    train: bool = False,
    main_agent_prompt: str | None = None,
) -> EvaluationResult:
    db_dir = get_db_base(train) / case.db_id
    agent = BirdEvaluationAgent(Path(db_dir), case.db_id, main_agent_prompt=main_agent_prompt)
    return agent.run_case(case)
