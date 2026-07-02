"""Regression checks for deterministic BIRD SQL output guard.

Usage:
  PYTHONPATH=. uv run python scripts/BIRD/test_hard_guard.py

These checks only parse stored SQL strings. They do not execute BIRD databases
or touch storage/graph state.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.BIRD.hard_guard import bird_sql_output_guard


DEV_PATH = REPO_ROOT / "workspace/baselines/bash_agent/data/bird_dev/dev.json"
PONTIS_RESULTS_PATH = (
    REPO_ROOT / "workspace/baselines/pontis/results/20260630_063613_bird_dev/results/results.jsonl"
)
DEEPEYE_RESULTS_PATH = (
    REPO_ROOT / "workspace/baselines/deepeye_sql/evaluation/"
    "20260605_deepeye_qwen24k_shards_ab_business/results.jsonl"
)


def _assert_true(value: bool, message: str):
    assert value, message


def _assert_equal(actual, expected, message: str):
    assert actual == expected, f"{message}\nexpected={expected!r}\nactual={actual!r}"


def _warning_text(sql: str, *, question: str = "", evidence: str = "") -> str:
    result = bird_sql_output_guard(sql, question=question, evidence=evidence)
    _assert_equal(result.strict, [], f"sample should not strict-block SQL: {sql}")
    return "\n".join(result.warnings)


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_unit_samples():
    _assert_true(
        bool(bird_sql_output_guard("SELECT 1").force),
        "Force guard should always be present for a parseable SQL.",
    )
    _assert_true(
        bool(bird_sql_output_guard("SELECT FROM").force),
        "Force guard should also be present for an unparseable SQL.",
    )
    _assert_true(
        "LEFT JOIN" in _warning_text("SELECT a.id FROM a LEFT JOIN b ON a.id = b.id"),
        "LEFT JOIN should warn instead of strict-blocking.",
    )
    _assert_true(
        "UNION" in _warning_text("SELECT 1 UNION SELECT 2"),
        "UNION should warn instead of strict-blocking.",
    )
    _assert_true(
        "GROUP_CONCAT" in _warning_text("SELECT GROUP_CONCAT(id) FROM t"),
        "GROUP_CONCAT should warn about compressing answer rows.",
    )
    _assert_true(
        "CASE 输出" in _warning_text("SELECT CASE WHEN x = 1 THEN 'Yes' ELSE 'No' END FROM t"),
        "Generic Yes/No CASE labels should warn.",
    )
    _assert_equal(
        bird_sql_output_guard("SELECT CASE WHEN x = 1 THEN 'YES' ELSE 'NO' END FROM t").warnings,
        [],
        "BIRD-style uppercase YES/NO labels should not warn by themselves.",
    )
    _assert_true(
        "LIKE 通配符"
        in _warning_text("SELECT id FROM users WHERE location LIKE '%India'", question="How many users were from India?"),
        "Unrequested wildcard LIKE should warn.",
    )
    _assert_true(
        "标量子查询"
        in _warning_text(
            "SELECT (SELECT COUNT(*) FROM foreign_data WHERE language = 'French') * 100.0 / "
            "(SELECT COUNT(*) FROM cards)",
            question="What is the percentage of cards whose language is French?",
        ),
        "Percentage scalar COUNT denominator should warn.",
    )
    _assert_true(
        "COUNT(DISTINCT CASE"
        in _warning_text(
            "SELECT 100.0 * COUNT(DISTINCT CASE WHEN x = 1 THEN id END) / COUNT(DISTINCT id) FROM t",
            question="What percentage of rows match?",
        ),
        "Percentage COUNT(DISTINCT CASE ...) should warn about entity grain.",
    )
    _assert_true(
        "乘以 100"
        in _warning_text("SELECT CAST(SUM(x) AS REAL) / COUNT(*) FROM t", question="What percentage matched?"),
        "Percentage result without multiplication by 100 should warn.",
    )
    _assert_true(
        "乘以 100"
        in _warning_text(
            "SELECT CAST(SUM(x) AS REAL) / COUNT(*) FROM t WHERE created_at > '2010-01-01'",
            question="What percentage matched?",
        ),
        "Dates containing 100 should not suppress the missing * 100 warning.",
    )
    _assert_true(
        "evidence 明确给出了"
        in _warning_text(
            "SELECT success_rate, item_code FROM measurements",
            question="What is the success rate?",
            evidence="success rate = `success_count` / `total_count` * 100%",
        ),
        "Existing percent/rate shortcut should warn when evidence gives a raw-column formula.",
    )
    _assert_true(
        "原始 code"
        in _warning_text(
            "SELECT SUBSTR(compound_identifier, 3, 5) AS requested_code FROM entities",
            question="What is the code for the entity?",
        ),
        "Deriving a requested raw code with string functions should warn.",
    )
    _assert_true(
        "原始 code"
        in _warning_text(
            "SELECT CASE WHEN raw_status = '+' THEN 'enabled' ELSE 'disabled' END FROM records",
            question="List the original status label for each record.",
        ),
        "Deriving requested raw status labels with CASE should warn.",
    )
    _assert_true(
        "SELECT *" in "\n".join(bird_sql_output_guard("SELECT * FROM account").strict),
        "SELECT * should remain a strict output-shape block.",
    )


def test_local_bird_regression_files():
    paths = [DEV_PATH, PONTIS_RESULTS_PATH, DEEPEYE_RESULTS_PATH]
    if not all(path.exists() for path in paths):
        missing = ", ".join(str(path) for path in paths if not path.exists())
        print(f"Skipping file-backed BIRD guard checks; missing: {missing}")
        return

    dev_rows = json.loads(DEV_PATH.read_text(encoding="utf-8"))
    pontis_rows = _load_jsonl(PONTIS_RESULTS_PATH)
    deepeye_rows = _load_jsonl(DEEPEYE_RESULTS_PATH)
    deepeye_by_qid = {row["question_id"]: row for row in deepeye_rows}

    gold_strict = []
    for row in dev_rows:
        result = bird_sql_output_guard(row["SQL"], question=row.get("question", ""), evidence=row.get("evidence", ""))
        if result.strict:
            gold_strict.append((row["question_id"], result.strict))
    _assert_equal(gold_strict, [], "BIRD dev gold SQL should have zero strict guard blocks.")

    pontis_correct_strict = []
    pontis_wrong_warn_or_strict = 0
    for row in pontis_rows:
        result = bird_sql_output_guard(
            row.get("predicted_sql") or "",
            question=row.get("question", ""),
            evidence=row.get("evidence", ""),
        )
        if row.get("business_correct") and result.strict:
            pontis_correct_strict.append((row["question_id"], result.strict))
        if not row.get("business_correct") and (result.strict or result.warnings):
            pontis_wrong_warn_or_strict += 1
    _assert_equal(
        pontis_correct_strict,
        [],
        "Pontis business-correct predictions should have zero strict guard blocks.",
    )
    _assert_true(
        pontis_wrong_warn_or_strict >= 150,
        "Guard should cover at least 150 of the 494 Pontis business-wrong cases without schema-field-specific rules.",
    )

    gap_rows = [
        row
        for row in pontis_rows
        if not row.get("business_correct") and deepeye_by_qid.get(row["question_id"], {}).get("business_correct")
    ]
    gap_warn_or_strict = 0
    gap_strict = 0
    for row in gap_rows:
        result = bird_sql_output_guard(
            row.get("predicted_sql") or "",
            question=row.get("question", ""),
            evidence=row.get("evidence", ""),
        )
        if result.strict:
            gap_strict += 1
        if result.strict or result.warnings:
            gap_warn_or_strict += 1
    _assert_equal(len(gap_rows), 173, "Expected the known Pontis-vs-DeepEye gap set size.")
    _assert_equal(gap_strict, 1, "Only SELECT * gap case should be strict-blocked in current run.")
    _assert_true(
        gap_warn_or_strict >= 65,
        "Guard should cover at least 65 of 173 known gap cases without schema-field-specific rules.",
    )


def main():
    test_unit_samples()
    test_local_bird_regression_files()
    print("hard_guard tests passed")


if __name__ == "__main__":
    main()
