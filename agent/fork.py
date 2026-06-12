"""Forked agent runner.

This mirrors the Claude Code fork shape: inherit the parent's rendered prompt,
full conversation context, model config, and exact tool pool, then append a
scoped fork directive. The fork has isolated messages/tool history/guardrails.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass

from agent.config import AgentSpec
from agent.guardrail import build_guardrails

logger = logging.getLogger(__name__)

FORK_WORKER_TAG = "pontis-fork-worker"


@dataclass
class ForkedAgentResult:
    label: str
    result: str
    rounds: int


def is_fork_context(messages: list) -> bool:
    marker = f"<{FORK_WORKER_TAG}>"
    return any(
        isinstance(msg, dict)
        and marker in str(msg.get("content") or "")
        for msg in messages
    )


def build_fork_directive(task: str) -> str:
    return f"""\
<{FORK_WORKER_TAG}>
You are a forked worker process. You are NOT the main agent.

Rules:
1. Execute only the directive below.
2. Use tools directly when evidence is needed.
3. Do not spawn sub-agents or forks.
4. Do not output final SQL for the main task.
5. Keep the report concise and factual.
6. Your response must begin with "Scope:".

Output format:
Scope: <one sentence>
Result: <key findings>
Relevant context: <refs, hints, and decisions, or none>
Recommended checks: <what the main agent should verify before final SQL>

Directive:
{task}
</{FORK_WORKER_TAG}>"""


def run_forked_agent(parent_agent, directive: str, *,
                     max_rounds: int = 5,
                     label: str = "fork") -> ForkedAgentResult:
    """Run a forked PontusAgent with inherited context and exact tools."""
    from agent.agent import PontusAgent

    active_projects = list(getattr(parent_agent.workspace, "active_projects", []) or [])
    fork_agent = PontusAgent(
        parent_agent.project_path,
        tools=parent_agent.tools,
        system_prompt=parent_agent.system_prompt,
        guardrails=_build_fork_guardrails(parent_agent, max_rounds),
        logger_name=f"{parent_agent.logger.name}.fork.{label}",
        active_projects=active_projects or None,
    )
    fork_agent.config = copy.deepcopy(parent_agent.config)
    fork_agent.messages = _legalize_tool_call_messages(copy.deepcopy(parent_agent.messages))
    fork_agent._tool_history = []
    fork_agent._empty_text_retries = 0

    result = ""
    for event in fork_agent.chat_stream(build_fork_directive(directive)):
        if event.get("type") == "done":
            result = event.get("content", "")
    return ForkedAgentResult(
        label=label,
        result=result or "",
        rounds=getattr(fork_agent, "_llm_rounds", 0),
    )


def _legalize_tool_call_messages(messages: list[dict]) -> list[dict]:
    """Return a valid chat snapshot for a fork started mid tool batch.

    The parent may trigger a fork immediately after one tool returns, while the
    same assistant message still has other tool calls waiting for results. Chat
    APIs reject that incomplete sequence, so the fork fills only the missing
    tool responses with inert placeholders. Orphan tool messages without a
    matching assistant tool_call are dropped.
    """
    fixed: list[dict] = []
    pending: list[str] = []

    def flush_missing() -> None:
        nonlocal pending
        for tool_call_id in pending:
            fixed.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": "Tool result was not available in the parent snapshot used for this fork.",
            })
        pending = []

    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            flush_missing()
            fixed.append(msg)
            pending = [
                tool_call_id
                for tool_call_id in (_tool_call_id(tc) for tc in (msg.get("tool_calls") or []))
                if tool_call_id
            ]
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id in pending:
                fixed.append(msg)
                pending.remove(tool_call_id)
            continue

        flush_missing()
        fixed.append(msg)

    flush_missing()
    return fixed


def _tool_call_id(tool_call) -> str | None:
    if isinstance(tool_call, dict):
        return tool_call.get("id")
    return getattr(tool_call, "id", None)


def _build_fork_guardrails(parent_agent, max_rounds: int):
    names = []
    for guardrail in getattr(parent_agent, "guardrails", []) or []:
        name = getattr(guardrail, "builder_name", None)
        if not name:
            continue
        if name not in names:
            names.append(name)

    spec = AgentSpec(
        project_path=parent_agent.project_path,
        tools=list(getattr(parent_agent.tools, "tool_names", [])),
        prompts=[],
        max_rounds=max_rounds,
    )
    guardrails = build_guardrails(spec, names)
    guardrails.append(_ForkRecursionBlock())
    return guardrails


class _ForkRecursionBlock:
    builder_name = "fork_recursion_block"

    def check(self, ctx):
        from agent.guardrail_api import CallVerdict

        result = {}
        for i, (name, _args) in enumerate(ctx.pending_calls):
            if name == "agent":
                result[i] = CallVerdict(
                    "block",
                    "You are already inside a fork worker. Do not spawn sub-agents; complete the fork directive directly.",
                )
        return result

    def post_tool(self, ctx, call_index, name, args, result):
        return None

    def drain_ready(self, ctx):
        return []
