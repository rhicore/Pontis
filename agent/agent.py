"""Pontis Agent - Interactive data analysis agent with tool calling."""
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from openai import OpenAI

from storage import Store
from agent.utils import load_agent_config
from agent.tools import build_registry
from agent.prompt import build_prompt
from agent.guardrail import Guardrail, AgentState, build_guardrails
from utils.llm import build_thinking_kwargs

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

_MAX_TOOL_RESULT = 8000


# ═══════════════════════════════════════════════════════════
#  Agent 创建配置 — 单一参数包
# ═══════════════════════════════════════════════════════════

@dataclass
class AgentSpec:
    """Agent 创建的完整参数包。"""
    project_path: str = ""
    mode: str = "readonly"
    effort: str = "mid"
    debug: bool = False
    max_rounds: Optional[int] = None
    disabled_tools: List[str] = field(default_factory=list)
    guardrails: List[Guardrail] = field(default_factory=list)


def create_agent(project_path: str, spec: AgentSpec = None) -> "PontisAgent":
    """工厂：根据 spec 自动组装 prompt + tools + guardrails。"""
    if spec is None:
        spec = AgentSpec()
    spec.project_path = project_path

    prompt = build_prompt(spec)
    tools = build_registry(spec)
    if not spec.guardrails:
        spec.guardrails = build_guardrails(spec)

    return PontisAgent(project_path, tools=tools, system_prompt=prompt,
                       guardrails=spec.guardrails)


# ═══════════════════════════════════════════════════════════
#  PontisAgent
# ═══════════════════════════════════════════════════════════

class PontisAgent:
    """Interactive agent that uses Pontis tools to analyze project data."""

    def __init__(self, project_path: str,
                 tools=None,
                 system_prompt: Optional[str] = None,
                 guardrails: Optional[List[Guardrail]] = None):
        self.project_path = project_path
        self.store = Store(project_path)
        self.config = load_agent_config(project_path)

        if not self.config["api_key"]:
            print("Error: No API key configured.")
            print("Set OPENAI_API_KEY env var, or create ~/.pontis/config.yml")
            sys.exit(1)

        self.client = OpenAI(
            api_key=self.config["api_key"],
            base_url=self.config["provider"],
            timeout=120.0,
        )

        self.tools = tools or build_registry(AgentSpec())
        self.system_prompt = system_prompt or build_prompt(AgentSpec(project_path=project_path))
        self.guardrails = guardrails or []
        self._tool_history: List[Tuple[str, dict, str]] = []
        self.messages = [{"role": "system", "content": self.system_prompt}]

    # ──────────────── LLM 调用 ────────────────

    def _call_llm(self):
        kwargs = {
            "model": self.config["model"],
            "messages": self.messages,
            "tools": self.tools.get_definitions(),
        }
        kwargs.update(build_thinking_kwargs(
            self.config.get("thinking", False),
            self.config.get("thinking_effort", "high"),
            max_tokens=self.config["max_tokens"],
            temperature=self.config["temperature"],
        ))
        return self.client.chat.completions.create(**kwargs)

    def _call_llm_round(self):
        """调用 LLM 并将 response 追加到消息历史。"""
        response = self._call_llm()
        msg = response.choices[0].message
        self.messages.append(self._msg_to_dict(msg))
        return msg

    def _msg_to_dict(self, msg) -> dict:
        d = msg.to_dict()
        if self.config.get("thinking") and msg.tool_calls:
            rc = getattr(msg, "reasoning_content", None)
            if rc:
                d["reasoning_content"] = rc
        return d

    # ──────────────── Guardrail ────────────────

    def _run_checks(self, rounds: int,
                    pending_calls: List[Tuple[str, dict]]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Returns (blocking_intervention, warning, blocker_class_name)."""
        if not self.guardrails:
            return None, None, None
        state = AgentState(messages=self.messages, rounds=rounds,
                           tool_history=self._tool_history, store=self.store)
        warning = None
        for g in self.guardrails:
            intervention = g.check(state, pending_calls)
            if intervention:
                if g.blocking:
                    return intervention, warning, g.__class__.__name__
                warning = intervention
        return None, warning, None

    # ──────────────── 工具执行 ────────────────

    def _execute_tool(self, name: str, arguments: dict, tool_call_id: str) -> str:
        """执行单个工具调用：执行 → 截断 → 记录。"""
        result = self.tools.execute(name, arguments, self.store)

        if len(result) > _MAX_TOOL_RESULT:
            result = result[:_MAX_TOOL_RESULT] + "\n... (truncated)"

        logger.info(f"Tool result [{name}]:\n  " + "\n  ".join(result.split("\n")))
        self.messages.append({
            "role": "tool", "tool_call_id": tool_call_id,
            "content": result,
        })
        self._tool_history.append((name, arguments, result))

        return result

    # ──────────────── 核心循环 ────────────────

    def _run_loop(self, user_input: str) -> Iterator[dict]:
        """核心 agent 循环。

        每轮：调用 LLM → guardrail 检查 → 拦截或执行工具 → 结束
        """
        self.messages.append({"role": "user", "content": user_input})
        rounds = 0

        while True:
            msg = self._call_llm_round()
            self._log_response(msg)

            # Guardrail 检查
            pending = [
                (tc.function.name, self._parse_args(tc.function.arguments))
                for tc in (msg.tool_calls or [])
            ]
            intervention, warning, blocked_by = self._run_checks(rounds, pending)

            # ── 拦截 ──
            if intervention:
                logger.info(f"Guardrail [{blocked_by}]: {intervention}")
                yield {"type": "blocked", "guardrail": blocked_by, "content": intervention}
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        self.messages.append({
                            "role": "tool", "tool_call_id": tc.id,
                            "content": intervention,
                        })
                else:
                    self.messages.append({"role": "user", "content": intervention})
                continue

            # ── 执行工具 ──
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.function.name
                    arguments = self._parse_args(tc.function.arguments)

                    yield {"type": "tool_call", "name": name,
                           "arguments": arguments, "id": tc.id}

                    result = self._execute_tool(name, arguments, tc.id)

                    yield {"type": "tool_result", "name": name,
                           "result": result, "id": tc.id}

                rounds += 1
                if warning:
                    logger.info(f"Guardrail warning: {warning}")
                    yield {"type": "warning", "content": warning}
                    self.messages.append({"role": "user", "content": warning})
                continue

            # ── 结束 ──
            if msg.content:
                logger.info(f"Agent done: {msg.content}")
            yield {"type": "done", "content": msg.content or ""}
            return

    # ──────────────── 公开接口 ────────────────

    def chat(self, user_input: str) -> str:
        """Send user input and return the agent's final text response."""
        result = ""
        for event in self._run_loop(user_input):
            if event["type"] == "tool_call":
                print(f"  \033[90m[{event['name']}({event['arguments']})]\033[0m")
            elif event["type"] == "done":
                result = event["content"]
        return result

    def chat_stream(self, user_input: str) -> Iterator[dict]:
        """Stream chat events for UI consumption."""
        yield from self._run_loop(user_input)

    # ──────────────── 工具方法 ────────────────

    @staticmethod
    def _log_response(msg):
        if msg.tool_calls:
            for tc in msg.tool_calls:
                logger.info(f"Tool call: {tc.function.name}({tc.function.arguments or '{}'})")
        elif msg.content:
            logger.info(f"LLM text: {msg.content[:300]}")

    @staticmethod
    def _parse_args(args_str: str) -> dict:
        try:
            return json.loads(args_str)
        except (json.JSONDecodeError, TypeError):
            return {}

    def reset_conversation(self):
        """Clear conversation history (keep system prompt) for a fresh session."""
        self.messages = [self.messages[0]]

    def run(self):
        """Run the interactive REPL."""
        print(f"\n\033[1mPontis Agent\033[0m — {self.project_path}")
        print(f"Model: {self.config['model']}")
        print(f"Guardrails: {[g.__class__.__name__ for g in self.guardrails]}")
        print(f"Type 'exit' or Ctrl+C to quit\n")

        while True:
            try:
                user_input = input("\033[36m你>\033[0m ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    print("Bye!")
                    break

                response = self.chat(user_input)
                print(f"\n\033[33m助手>\033[0m {response}\n")

            except KeyboardInterrupt:
                print("\nBye!")
                break
            except EOFError:
                print("\nBye!")
                break
