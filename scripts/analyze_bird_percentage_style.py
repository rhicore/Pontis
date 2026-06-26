"""Inspect BIRD dev golden SQL percentage/rate/ratio style."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEV_JSON = ROOT / "data" / "bird_dev" / "dev.json"


PATTERNS = {
    "percent_word": re.compile(r"\b(percent|percentage)\b", re.I),
    "rate": re.compile(r"\brate\b", re.I),
    "ratio": re.compile(r"\bratio\b", re.I),
}


def has_division(sql: str) -> bool:
    return "/" in sql or bool(re.search(r"\bDIVIDE\s*\(", sql, re.I))


def has_times_100(sql: str) -> bool:
    compact = re.sub(r"\s+", " ", sql)
    return bool(re.search(r"(\*\s*100(?:\.0)?|100(?:\.0)?\s*\*)", compact, re.I))


def main() -> None:
    items = json.loads(DEV_JSON.read_text())
    print(f"total items: {len(items)}")

    for key, pattern in PATTERNS.items():
        rows = []
        for index, item in enumerate(items):
            question = item.get("question") or ""
            evidence = item.get("evidence") or ""
            sql = item.get("SQL") or ""
            if not pattern.search(f"{question}\n{evidence}"):
                continue
            rows.append(
                {
                    "index": index,
                    "db_id": item.get("db_id"),
                    "question": question,
                    "evidence": evidence,
                    "sql": sql,
                    "has_division": has_division(sql),
                    "has_times_100": has_times_100(sql),
                }
            )

        violations = [
            row for row in rows if row["has_division"] and not row["has_times_100"]
        ]
        print()
        print(
            f"{key}: count={len(rows)} "
            f"division={sum(row['has_division'] for row in rows)} "
            f"times100={sum(row['has_times_100'] for row in rows)} "
            f"division_without_100={len(violations)}"
        )
        for row in violations:
            sql = re.sub(r"\s+", " ", row["sql"])[:220]
            print(
                f"  idx={row['index']} db={row['db_id']} "
                f"Q={row['question']!r} SQL={sql}"
            )


if __name__ == "__main__":
    main()
