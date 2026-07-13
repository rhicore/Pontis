#!/usr/bin/env python3
"""Focused regression tests for tool-loop finalization and progress controls."""

import json
import logging
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.agent import PontusAgent
from agent.guardrail.round_limit import RoundLimit
from agent.guardrail.tool_use_check import ToolUseCheck
from agent.guardrail_api import GuardrailContext


def _tool_call(call_id: str, sql: str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="query",
            arguments=json.dumps({"ref": "demo.sqlite:db", "sql": sql}),
        ),
    )


class _LoopAgent(PontusAgent):
    def __init__(self):
        self.messages = []
        self.guardrails = [RoundLimit(1)]
        self.workspace = None
        self._tool_history = []
        self._last_guardrail_trace = []
        self._trace_callback = None
        self._empty_text_retries = 0
        self.logger = logging.getLogger("test_tool_control")
        self.tools_enabled = []
        self.executions = 0

    def _call_llm_round(self, *, tools_enabled=True):
        self.tools_enabled.append(tools_enabled)
        turn = len(self.tools_enabled)
        if turn <= 2:
            msg = SimpleNamespace(content=None, tool_calls=[_tool_call(str(turn), "SELECT 1")])
        else:
            msg = SimpleNamespace(content="final", tool_calls=[])
        self.messages.append({"role": "assistant", "content": msg.content})
        return msg

    def _execute_tool(self, name, arguments, tool_call_id):
        self.executions += 1
        result = "1"
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
        self._tool_history.append((name, arguments, result))
        return result

    def _log_response(self, msg):
        return None


def test_round_limit_enters_tool_free_final_round():
    agent = _LoopAgent()
    events = list(agent._drive_loop())
    assert agent.tools_enabled == [True, True, False]
    assert agent.executions == 1
    assert events[-1] == {"type": "done", "content": "final"}


def test_tool_use_check_reuses_exact_query():
    guardrail = ToolUseCheck({"query": 4})
    ctx = GuardrailContext(
        messages=[],
        tool_history=[("query", {"ref": "demo.sqlite:db", "sql": "SELECT 1"}, "1")],
        workspace=None,
        rounds=1,
        pending_calls=[("query", {"ref": "demo.sqlite:db", "sql": "select 1;"})],
    )
    verdict = guardrail.check(ctx)[0]
    assert verdict.action == "block"
    assert not verdict.finalize
    assert "已有结果" in verdict.message


def test_tool_use_check_finalizes_at_budget():
    guardrail = ToolUseCheck({"query": 1})
    ctx = GuardrailContext(
        messages=[],
        tool_history=[("query", {"ref": "demo.sqlite:db", "sql": "SELECT 1"}, "1")],
        workspace=None,
        rounds=1,
        pending_calls=[("query", {"ref": "demo.sqlite:db", "sql": "SELECT 2"})],
    )
    verdict = guardrail.check(ctx)[0]
    assert verdict.action == "block"
    assert verdict.finalize


if __name__ == "__main__":
    test_round_limit_enters_tool_free_final_round()
    test_tool_use_check_reuses_exact_query()
    test_tool_use_check_finalizes_at_budget()
    print("3/3 tool-control tests passed")
