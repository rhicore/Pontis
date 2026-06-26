"""BIRD evaluation agent that drives conversations with Pontis."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent.utils import load_agent_config
from scripts.BIRD.benchmark_runtime import execute_sql, extract_sql, find_db_file, format_execution_result, is_correct
from utils.context_dump import dump_llm_context, reset_context_dump_meta, set_context_dump_meta

from .bird_prompts import (
    BIRD_REVIEW_PROMPT,
    SELECT_RESULT_TABLE_REVIEW_PROMPT,
    build_business_initial_request,
    build_pontis_plan_request,
    build_select_result_table_review_request,
    build_sql_output_review_request,
)
from .models import BirdCase, CandidateReport, EvaluationResult
from .pontis_worker import PontisSqlWorker
from .sql_output_guard import (
    bird_sql_output_guard,
    format_bird_sql_output_guard_feedback,
    format_bird_sql_output_guard_warning,
)


EVALUATION_AGENT_SYSTEM_PROMPT = """\
你是 BIRD SQL 输出审查员。

你的职责是事后审查 DBA agent 的候选 SQL 是否符合 BIRD 要求。

如果候选 SQL 符合要求，只输出 OK，不要解释。
如果候选 SQL 不符合要求，不要输出 OK，直接给出拒绝理由和修改建议。

""" + BIRD_REVIEW_PROMPT


SELECT_RESULT_TABLE_REVIEW_SYSTEM_PROMPT = """\
你是 BIRD SELECT 结果表审查员。

你的职责是审查候选 SQL 的 SELECT 结果表是否符合题面要求。只审查 SELECT 列、列顺序、答案对象、额外输出列、漏选列和结果表形状。

如果 SELECT 结果表符合要求，只输出 OK，不要解释。
如果 SELECT 结果表不符合要求，不要输出 OK，直接给出拒绝理由和修改建议。

""" + SELECT_RESULT_TABLE_REVIEW_PROMPT


class BirdEvaluationLLM:
    """LLM evaluation agent that plays the BIRD business/evaluator role."""

    def __init__(self, project_path: Path):
        self.config = load_agent_config(str(project_path))
        if not self.config["api_key"]:
            raise RuntimeError("No API key configured for BIRD evaluation agent.")
        self.client = OpenAI(
            api_key=self.config["api_key"],
            base_url=self.config["provider"],
            timeout=120.0,
        )

    def answer_judge_question(self, question: str, context: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": EVALUATION_AGENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "SQL plan judge 正在裁决多个候选方案，需要 BIRD evaluation agent "
                        "给出业务/评测偏好。请只回答这个问题，不调用工具。\n\n"
                        f"裁决问题：{question}\n\n"
                        f"上下文：\n{context}"
                    ),
                },
            ],
        }
        if self.config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = self.config.get("temperature", 0.3)
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    def review_sql_output(self, case: BirdCase, sql: str, execution_preview: str | None = None) -> str | None:
        kwargs: dict[str, Any] = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": EVALUATION_AGENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_sql_output_review_request(case, sql, execution_preview=execution_preview),
                },
            ],
        }
        if self.config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = self.config.get("temperature", 0.3)
        dump_llm_context("evaluation_agent", kwargs)
        response = self.client.chat.completions.create(**kwargs)
        feedback = (response.choices[0].message.content or "").strip()
        if not feedback or feedback.upper().startswith("OK"):
            return None
        return feedback

    def review_select_result_table(self, case: BirdCase, sql: str, execution_preview: str | None = None) -> str | None:
        kwargs: dict[str, Any] = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": SELECT_RESULT_TABLE_REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_select_result_table_review_request(case, sql, execution_preview=execution_preview),
                },
            ],
        }
        if self.config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = self.config.get("temperature", 0.3)
        dump_llm_context("select_result_table_evaluation_agent", kwargs)
        response = self.client.chat.completions.create(**kwargs)
        feedback = (response.choices[0].message.content or "").strip()
        if not feedback or feedback.upper().startswith("OK"):
            return None
        return feedback


class BirdEvaluationAgent:
    """Dataset-level agent that asks Pontis questions and reviews the answers."""

    def __init__(self, db_dir: Path, db_id: str, main_agent_prompt: str | None = None):
        self.db_dir = Path(db_dir)
        self.db_id = db_id
        self.db_path = find_db_file(self.db_dir)
        if not self.db_path:
            raise FileNotFoundError(f"No SQLite database found under {self.db_dir}")
        challenge_count = int(os.environ.get("PONTIS_SCHEMA_CHALLENGE_COUNT", "0") or "0")
        self.pontis = PontisSqlWorker(
            self.db_dir,
            db_id,
            main_agent_prompt=main_agent_prompt,
            schema_challenge_count=challenge_count,
            judge_question_callback=self._answer_judge_question,
        )
        self.evaluation_agent = BirdEvaluationLLM(self.db_dir)
        self._current_case: BirdCase | None = None
        self._warned_guard_messages: set[str] = set()

    def _answer_judge_question(self, question: str, context: str) -> str:
        return self.evaluation_agent.answer_judge_question(question, context)

    def run_case(self, case: BirdCase) -> EvaluationResult:
        """Run one BIRD case by conversing with the generic Pontis agent."""
        token = set_context_dump_meta(
            run_id=os.environ.get("PONTIS_CONTEXT_RUN_ID"),
            db_id=case.db_id,
            question_id=case.question_id,
        )
        try:
            return self._run_case(case)
        finally:
            reset_context_dump_meta(token)

    def _run_case(self, case: BirdCase) -> EvaluationResult:
        """Run one BIRD case with Pontis first, then post-hoc SQL reviewers."""
        attempts: list[CandidateReport] = []
        candidate: CandidateReport | None = None
        self._current_case = case
        self._warned_guard_messages = set()
        predicted_execution: set | str = "PARSE_ERROR"
        turn = 1
        llm_review_rounds = 0
        max_llm_review_rounds = 2

        request = build_pontis_plan_request(case)
        while True:
            if candidate is None:
                candidate = self.pontis.plan(turn, request)
            else:
                candidate = self.pontis.reject(turn, request)
            attempts.append(candidate)
            turn += 1

            sql = self._candidate_sql(candidate)
            if not sql:
                request = self._combine_review_feedback(
                    evaluation_feedback="",
                    select_feedback=None,
                    guard_feedback=(
                        "SQL 输出硬拦截：候选结果没有可解析 SQL；"
                        "修改方式：重新提交唯一的 ```sql 代码块。"
                    ),
                )
                continue

            hard_guard_feedback = self._guard_feedback(sql, include_warnings=False)
            if hard_guard_feedback:
                request = self._combine_review_feedback(
                    evaluation_feedback="",
                    select_feedback=None,
                    guard_feedback=hard_guard_feedback,
                )
                continue

            candidate_execution = execute_sql(self.db_path, sql)
            if isinstance(candidate_execution, str):
                request = (
                    "SQL 执行失败，必须修改后重新提交一个唯一的 ```sql 代码块。\n\n"
                    f"执行错误：{candidate_execution}"
                )
                continue
            execution_preview = format_execution_result(candidate_execution, limit=8)

            review_feedback = None
            if llm_review_rounds < max_llm_review_rounds:
                evaluation_feedback = (
                    None if self._count_distinct_warning_was_resolved(sql)
                    else self._sql_output_feedback(sql, execution_preview=execution_preview)
                )
                select_feedback = self._select_result_table_feedback(sql, execution_preview=execution_preview)
                if evaluation_feedback or select_feedback:
                    llm_review_rounds += 1
                    review_feedback = self._combine_review_feedback(
                        evaluation_feedback=evaluation_feedback or "",
                        select_feedback=select_feedback,
                        guard_feedback=None,
                    )

            warning_feedback = self._guard_feedback(sql, include_warnings=True)
            if review_feedback and warning_feedback:
                review_feedback = review_feedback + "\n\n确定性 SQL 输出检查：\n" + warning_feedback
            elif warning_feedback:
                review_feedback = self._combine_review_feedback(
                    evaluation_feedback="",
                    select_feedback=None,
                    guard_feedback=warning_feedback,
                )

            if review_feedback:
                request = review_feedback
                continue

            candidate = self._with_predicted_sql(
                candidate,
                sql,
                action="review_approved_sql",
            )
            predicted_execution = candidate_execution
            break

        if candidate is None:
            raise RuntimeError("No candidate generated")

        golden_execution = execute_sql(self.db_path, case.golden_sql) if case.golden_sql else None
        correct = bool(golden_execution is not None and is_correct(predicted_execution, golden_execution))
        result = (
            "CORRECT" if correct
            else "PARSE_ERROR" if candidate.predicted_sql is None
            else "EXEC_ERROR" if isinstance(predicted_execution, str)
            else "WRONG"
        )
        return EvaluationResult(
            case=case,
            candidate=candidate,
            result=result,
            correct=correct,
            predicted_execution=predicted_execution,
            golden_execution=golden_execution,
            attempts=attempts,
        )

    @staticmethod
    def _extract_sql_from_exit_plan(candidate: CandidateReport) -> str | None:
        args = (candidate.exit_plan_request or {}).get("arguments") or {}
        return extract_sql(str(args.get("plan") or ""))

    @staticmethod
    def _with_predicted_sql(candidate: CandidateReport, sql: str, *, action: str) -> CandidateReport:
        return CandidateReport(
            attempt=candidate.attempt,
            action=action,
            request=candidate.request,
            raw_response=candidate.raw_response,
            predicted_sql=sql,
            elapsed=candidate.elapsed,
            efficiency=candidate.efficiency,
            exit_plan_requested=candidate.exit_plan_requested,
            exit_plan_request=candidate.exit_plan_request,
            challenge_reports=candidate.challenge_reports,
            judge_report=candidate.judge_report,
        )

    def _candidate_sql(self, candidate: CandidateReport) -> str | None:
        if candidate.predicted_sql:
            return candidate.predicted_sql
        if candidate.exit_plan_requested:
            return self._extract_sql_from_exit_plan(candidate)
        return extract_sql(candidate.raw_response)

    @staticmethod
    def _combine_review_feedback(
        *,
        evaluation_feedback: str,
        select_feedback: str | None,
        guard_feedback: str | None,
    ) -> str:
        sections = ["裁决结果：拒绝当前候选 SQL plan。"]
        if evaluation_feedback:
            sections.append("SQL 输出审查：\n" + evaluation_feedback)
        if select_feedback:
            sections.append("SELECT 结果表审查：\n" + select_feedback)
        if guard_feedback:
            sections.append("确定性 SQL 输出检查：\n" + guard_feedback)
        return "\n\n".join(sections)

    def _select_result_table_feedback(self, sql: str, execution_preview: str | None = None) -> str | None:
        case = self._current_case
        if not case:
            return None
        return self.evaluation_agent.review_select_result_table(case, sql, execution_preview=execution_preview)

    def _sql_output_feedback(self, sql: str, execution_preview: str | None = None) -> str | None:
        case = self._current_case
        if not case:
            return None
        return self.evaluation_agent.review_sql_output(case, sql, execution_preview=execution_preview)

    def _guard_feedback(
        self,
        sql: str,
        *,
        include_warnings: bool = True,
        mark_warnings: bool = True,
    ) -> str | None:
        case = self._current_case
        result = bird_sql_output_guard(
            sql,
            question=case.question if case else "",
            evidence=case.evidence if case else "",
        )
        hard = [*result.hard]
        if case:
            hard.extend(self._literal_value_feedback(sql))
        if hard:
            return format_bird_sql_output_guard_feedback(hard)
        if not include_warnings:
            return None
        unseen_warnings = [
            warning for warning in result.warnings
            if warning not in self._warned_guard_messages
        ]
        if not unseen_warnings:
            return None
        if mark_warnings:
            self._warned_guard_messages.update(unseen_warnings)
        return format_bird_sql_output_guard_warning(unseen_warnings)

    def _count_distinct_warning_was_resolved(self, sql: str) -> bool:
        warned = any("COUNT(DISTINCT" in message for message in self._warned_guard_messages)
        if not warned:
            return False
        return not re.search(r"\bCOUNT\s*\(\s*DISTINCT\b", sql, re.IGNORECASE)

    _TABLE_ALIAS_RE = re.compile(
        r"\b(?:FROM|JOIN)\s+(?P<table>[`\"\[]?[A-Za-z_][\w]*[`\"\]]?)"
        r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_][\w]*))?",
        re.IGNORECASE,
    )
    _STRING_EQ_RE = re.compile(
        r"(?P<ref>(?:[A-Za-z_][\w]*\.)?[`\"\[]?[A-Za-z_][\w]*[`\"\]]?)\s*=\s*'(?P<value>[^']*)'",
        re.IGNORECASE,
    )

    def _literal_value_feedback(self, sql: str) -> list[str]:
        aliases = self._sql_table_aliases(sql)
        if not aliases:
            return []
        messages: list[str] = []
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return []
        try:
            schema = self._table_columns(conn, set(aliases.values()))
            for match in self._STRING_EQ_RE.finditer(sql):
                ref = match.group("ref")
                value = match.group("value")
                resolved = self._resolve_column_ref(ref, aliases, schema)
                if not resolved or not value:
                    continue
                table, column = resolved
                replacement = self._existing_value_suggestion(conn, table, column, value)
                if not replacement:
                    continue
                if replacement.endswith("%"):
                    messages.append(
                        f"`{table}.{column} = '{value}'` 使用了该列中不存在的时间字符串；修改方式：按数据库存储格式改为 `{table}.{column} LIKE '{replacement}'`。"
                    )
                    continue
                messages.append(
                    f"`{table}.{column} = '{value}'` 使用了该列中不存在的字符串值；修改方式：使用数据库中的实际值 `'{replacement}'`。"
                )
        finally:
            conn.close()
        return self._dedupe_messages(messages)

    def _sql_table_aliases(self, sql: str) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for match in self._TABLE_ALIAS_RE.finditer(sql):
            table = self._strip_identifier_quotes(match.group("table"))
            alias = match.group("alias")
            aliases[table] = table
            if alias and alias.upper() not in {"ON", "WHERE", "JOIN", "INNER", "LEFT", "GROUP", "ORDER", "LIMIT"}:
                aliases[self._strip_identifier_quotes(alias)] = table
        return aliases

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, tables: set[str]) -> dict[str, set[str]]:
        schema: dict[str, set[str]] = {}
        for table in tables:
            try:
                rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            except sqlite3.Error:
                continue
            schema[table] = {str(row[1]) for row in rows}
        return schema

    def _resolve_column_ref(
        self,
        ref: str,
        aliases: dict[str, str],
        schema: dict[str, set[str]],
    ) -> tuple[str, str] | None:
        clean = self._strip_identifier_quotes(ref)
        if "." in clean:
            alias, column = clean.split(".", 1)
            table = aliases.get(alias)
            if table and column in schema.get(table, set()):
                return table, column
            return None
        matches = [(table, clean) for table, columns in schema.items() if clean in columns]
        return matches[0] if len(matches) == 1 else None

    def _existing_value_suggestion(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        value: str,
    ) -> str | None:
        if self._value_exists(conn, table, column, value):
            return None
        exact_case = self._single_value(
            conn,
            table,
            column,
            f'LOWER("{column}") = LOWER(?)',
            (value,),
        )
        if exact_case:
            return exact_case
        time_prefix = self._time_literal_prefix_suggestion(conn, table, column, value)
        if time_prefix:
            return time_prefix
        if ":" in value or len(value) < 3:
            return None
        distinct_count = self._distinct_count(conn, table, column)
        if distinct_count is None or distinct_count > 500:
            return None
        return self._shortest_prefix_value(conn, table, column, value)

    @staticmethod
    def _value_exists(conn: sqlite3.Connection, table: str, column: str, value: str) -> bool:
        try:
            row = conn.execute(
                f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1',
                (value,),
            ).fetchone()
        except sqlite3.Error:
            return True
        return row is not None

    @staticmethod
    def _single_value(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        where_sql: str,
        params: tuple[str, ...],
    ) -> str | None:
        try:
            rows = conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" WHERE {where_sql} AND "{column}" IS NOT NULL LIMIT 2',
                params,
            ).fetchall()
        except sqlite3.Error:
            return None
        if len(rows) == 1 and rows[0][0] is not None:
            return str(rows[0][0])
        return None

    @staticmethod
    def _distinct_count(conn: sqlite3.Connection, table: str, column: str) -> int | None:
        try:
            row = conn.execute(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"').fetchone()
        except sqlite3.Error:
            return None
        return int(row[0]) if row else None

    @staticmethod
    def _shortest_prefix_value(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        value: str,
    ) -> str | None:
        try:
            rows = conn.execute(
                f'''
                SELECT DISTINCT "{column}"
                FROM "{table}"
                WHERE LOWER("{column}") LIKE LOWER(?) AND "{column}" IS NOT NULL
                ORDER BY LENGTH("{column}") ASC, "{column}" ASC
                LIMIT 2
                ''',
                (value + "%",),
            ).fetchall()
        except sqlite3.Error:
            return None
        if not rows or rows[0][0] is None:
            return None
        if len(rows) == 1:
            return str(rows[0][0])
        first = str(rows[0][0])
        second = str(rows[1][0])
        return first if len(first) < len(second) else None

    @staticmethod
    def _time_literal_prefix_suggestion(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        value: str,
    ) -> str | None:
        match = re.fullmatch(r"0+:(\d{1,2}):(\d{2})", value)
        if not match:
            return None
        prefix = f"{int(match.group(1))}:{match.group(2)}"
        try:
            rows = conn.execute(
                f'''
                SELECT DISTINCT "{column}"
                FROM "{table}"
                WHERE "{column}" LIKE ? AND "{column}" IS NOT NULL
                ORDER BY "{column}" ASC
                LIMIT 2
                ''',
                (prefix + "%",),
            ).fetchall()
        except sqlite3.Error:
            return None
        if not rows or rows[0][0] is None:
            return None
        if len(rows) == 1:
            return prefix + "%"
        first = str(rows[0][0])
        second = str(rows[1][0])
        return prefix + "%" if first.startswith(prefix) and second.startswith(prefix) else None

    @staticmethod
    def _strip_identifier_quotes(identifier: str) -> str:
        return identifier.strip().strip("`\"[]")

    @staticmethod
    def _dedupe_messages(messages: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for message in messages:
            if message in seen:
                continue
            seen.add(message)
            deduped.append(message)
        return deduped
