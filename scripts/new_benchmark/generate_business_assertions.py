#!/usr/bin/env python3
"""Draft LLM prompt for decomposing a Text-to-SQL sample into business checks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.append(path_text)

from agent.utils import load_agent_config  # noqa: E402
from utils.llm import LLMClient  # noqa: E402


SYSTEM_PROMPT = """你是业务评测样本拆分员。

输入是一个 Text-to-SQL 样本，包含 question、evidence、gold_sql 和 gold_result。你的任务是把它拆成若干个可自动核验的子问题。每个子问题对应一个单元格标准答案。请返回 JSON。"""


USER_PROMPT_TEMPLATE = """请把样本拆成业务评测子问题。

工作方法：
1. 用 question 和 evidence 理解用户要查什么。
2. 用 gold_sql 和 gold_result 确定标准答案、排序、行数、重复值、单位和数值尺度。
3. 每个子问题聚焦一个事实，答案写入 expected_cell，类型为 string、number、boolean 或 null。
4. expected_cell 来自一个 gold_result 单元格或 gold_result.row_count；跨列概念选择其中最关键的组成单元格来核验。
5. 自行选择少量关键子问题，优先检测最能判断业务正确性的事实。
6. 复杂报表通常生成 3 到 8 个子问题；简单单值题通常生成 1 个。
7. 关键子问题应覆盖结果对象、行数或重复值、排序/top-k 位置、重要列值、标签值、数值口径中最容易出错的部分。
8. gold_result.truncated 为 true 时，assertions 选择 row_count 以及可见行中的关键单元格；完整列表、全表存在性、全表覆盖性、distinct 数等整体事实写入 review_reason。
9. 重复值有意义；需要检查重复口径时，用第几行、第几名或出现次数表达。
10. 百分比和小数保留 gold_result 的尺度，并填写 unit_or_scale 和 tolerance。
11. 题意、evidence、gold_sql 或 gold_result 口径存在冲突时，以 gold_result 的可核验事实生成子问题，并把 needs_review 设为 true。

comparison 取值：
- "exact": 字符串、整数、日期、原始标签等精确比较。
- "numeric_close": 普通数值比较。
- "percentage_close": 百分比或比例比较。
- "boolean_exact": true/false 比较。

输出 JSON：
{
  "sample_id": string | null,
  "db_id": string | null,
  "business_goal": string,
  "assertions": [
    {
      "id": "A1",
      "subquestion": string,
      "expected_cell": string | number | boolean | null,
      "gold_result_ref": {"row_index_1based": number | null, "column": string | null} | null,
      "comparison": "exact" | "numeric_close" | "percentage_close" | "boolean_exact",
      "unit_or_scale": string | null,
      "tolerance": number | null,
      "source": "question" | "evidence" | "result" | "ambiguous",
      "note": string
    }
  ],
  "needs_review": boolean,
  "review_reason": string | null
}

样本：
{sample_json}
"""


FIELD_ALIASES = {
    "sample_id": ("sample_id", "id", "question_id", "qid"),
    "db_id": ("db_id", "database_id", "db"),
    "question": ("question", "query", "utterance"),
    "evidence": ("evidence", "hint", "context"),
    "gold_sql": ("gold_sql", "golden_sql", "sql", "SQL"),
    "gold_result": (
        "gold_result",
        "gold_execution_result",
        "execution_result",
        "result",
        "answer",
    ),
}


def pick_first(raw: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def normalize_sample(raw: dict[str, Any]) -> dict[str, Any]:
    """Map common dataset field names into the prompt-facing sample shape."""
    sample = {field: pick_first(raw, aliases) for field, aliases in FIELD_ALIASES.items()}
    extra = {
        key: value
        for key, value in raw.items()
        if key
        not in {
            alias
            for aliases in FIELD_ALIASES.values()
            for alias in aliases
        }
    }
    if extra:
        sample["extra_fields"] = extra
    return sample


def read_sample(path: Path, line_index: int = 0) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"empty sample file: {path}")

    if path.suffix == ".jsonl":
        lines = [line for line in text.splitlines() if line.strip()]
        if line_index < 0 or line_index >= len(lines):
            raise IndexError(f"--line-index {line_index} out of range for {len(lines)} jsonl lines")
        raw = json.loads(lines[line_index])
    else:
        raw = json.loads(text)
        if isinstance(raw, list):
            if line_index < 0 or line_index >= len(raw):
                raise IndexError(f"--line-index {line_index} out of range for {len(raw)} samples")
            raw = raw[line_index]

    if not isinstance(raw, dict):
        raise TypeError("sample must be a JSON object")
    return normalize_sample(raw)


def build_messages(sample: dict[str, Any]) -> list[dict[str, str]]:
    sample_json = json.dumps(sample, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.replace("{sample_json}", sample_json)},
    ]


def format_prompt(messages: list[dict[str, str]]) -> str:
    parts = []
    for message in messages:
        parts.append(f"[{message['role'].upper()}]\n{message['content']}")
    return "\n\n".join(parts)


def extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        return json.loads(fenced.group(1))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise TypeError("LLM output must be a JSON object")
    return parsed


def make_client(project_path: Path) -> LLMClient:
    cfg = load_agent_config(str(project_path))
    return LLMClient(
        api_key=cfg["api_key"],
        provider=cfg["provider"],
        model=cfg["model"],
        thinking=cfg.get("thinking", True),
        thinking_effort=cfg.get("thinking_effort", "high"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate atomic business assertions from a query-SQL-result sample."
    )
    parser.add_argument("--sample-json", type=Path, help="Path to a JSON or JSONL sample file.")
    parser.add_argument("--line-index", type=int, default=0, help="Sample index for JSONL/list input.")
    parser.add_argument("--project-path", type=Path, default=PONTIS_ROOT, help="Pontis project path.")
    parser.add_argument("--output", type=Path, help="Write parsed JSON output to this path.")
    parser.add_argument("--print-template", action="store_true", help="Print the prompt template and exit.")
    parser.add_argument("--print-prompt", action="store_true", help="Print the filled prompt and exit.")
    parser.add_argument("--raw", action="store_true", help="Print raw LLM output instead of parsed JSON.")
    args = parser.parse_args()

    if args.print_template:
        print(format_prompt(build_messages({"sample_id": "Q_example"})))
        return 0

    if not args.sample_json:
        parser.error("--sample-json is required unless --print-template is used")

    sample = read_sample(args.sample_json, line_index=args.line_index)
    messages = build_messages(sample)

    if args.print_prompt:
        print(format_prompt(messages))
        return 0

    client = make_client(args.project_path)
    response = client.complete_messages(messages)
    if args.raw:
        print(response)
        return 0

    parsed = extract_json_object(response)
    output_text = json.dumps(parsed, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text + "\n", encoding="utf-8")
    else:
        print(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
