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

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════
#  Agent 创建配置 — 单一参数包
# ═══════════════════════════════════════════════════════════

@dataclass
class AgentSpec:
    """Agent 创建的完整参数包。

    所有组装逻辑（prompt、tools、guardrails）都从这个 spec 派生。
    新增参数只需加字段，然后在 PROMPT_LAYERS、build_registry、
    build_guardrails 中使用。
    """
    project_path: str = ""
    mode: str = "readonly"              # readonly | writer | sub_agent | benchmark
    effort: str = "mid"                 # low | mid | high | max
    debug: bool = False
    max_rounds: Optional[int] = None    # None = 按 effort 推导
    disabled_tools: List[str] = field(default_factory=list)
    guardrails: List[Guardrail] = field(default_factory=list)


def create_agent(project_path: str, spec: AgentSpec = None) -> "PontisAgent":
    """工厂：根据 spec 自动组装 prompt + tools + guardrails。"""
    if spec is None:
        spec = AgentSpec()
    spec.project_path = project_path

    # 1. prompt — 声明式层组装
    prompt = build_prompt(spec)

    # 2. tools — spec 驱动注册
    tools = build_registry(spec)

    # 3. guardrails — 如果 spec 没有指定，从 mode/effort 派生默认集
    if not spec.guardrails:
        spec.guardrails = build_guardrails(spec)

    agent = PontisAgent(project_path, tools=tools, system_prompt=prompt,
                        guardrails=spec.guardrails)
    return agent


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
        self._tool_history: List[Tuple[str, dict, str]] = []  # 累积的工具调用历史
        self.messages = [
            {"role": "system", "content": self.system_prompt},
        ]

    # ──────────────── LLM 调用 ────────────────

    def _call_llm(self):
        """调用 LLM，返回 response。"""
        return self.client.chat.completions.create(
            model=self.config["model"],
            messages=self.messages,
            tools=self.tools.get_definitions(),
            max_tokens=self.config["max_tokens"],
            temperature=self.config["temperature"],
        )

    # ──────────────── 工具执行 ────────────────

    def _execute_tool_calls(self, tool_calls) -> List[Tuple[str, dict, str]]:
        """执行一组工具调用，返回 [(name, arguments, result), ...]。"""
        results = []
        for tool_call in tool_calls:
            name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            print(f"  \033[90m[{name}({arguments})]\033[0m")
            args_str = json.dumps(arguments, ensure_ascii=False)
            logger.info(f"Tool call: {name}({args_str})")
            result = self.tools.execute(name, arguments, self.store)

            if len(result) > 8000:
                result = result[:8000] + "\n... (truncated)"

            indented = "\n  ".join(result.split("\n"))
            logger.info(f"Tool result [{name}]:\n  {indented}")
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })
            results.append((name, arguments, result))
        return results

    # ──────────────── Guardrail 检查 ────────────────

    def _run_checks(self, rounds: int,
                    pending_calls: List[Tuple[str, dict]]) -> Optional[str]:
        """运行所有 guardrail 检查。返回干预文本或 None。"""
        if not self.guardrails:
            return None
        state = AgentState(messages=self.messages, rounds=rounds,
                           tool_history=self._tool_history, store=self.store)
        for g in self.guardrails:
            intervention = g.check(state, pending_calls)
            if intervention:
                logger.info(f"Guardrail {g.__class__.__name__}: intervention")
                return intervention
        return None

    # ──────────────── 主对话接口 ────────────────

    def chat(self, user_input: str) -> str:
        """Send user input and return the agent's response."""
        self.messages.append({"role": "user", "content": user_input})
        rounds = 0

        while True:
            response = self._call_llm()
            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            # ── Guardrail 检查（始终运行）──
            pending = [
                (tc.function.name, self._parse_args(tc.function.arguments))
                for tc in (msg.tool_calls or [])
            ]
            intervention = self._run_checks(rounds, pending)
            if intervention:
                self.messages.append({"role": "user", "content": intervention})
                continue  # 跳过工具执行，让 LLM 重新思考

            # ── 执行工具调用 ──
            if msg.tool_calls:
                tool_history = self._execute_tool_calls(msg.tool_calls)
                self._tool_history.extend(tool_history)
                rounds += 1
                logger.debug(f"Round {rounds}")

            # 无工具调用 → 结束
            if not msg.tool_calls:
                if msg.content:
                    logger.info(f"Agent done: {msg.content}")
                return msg.content or ""

    def chat_stream(self, user_input: str) -> Iterator[dict]:
        """Stream chat events: tool calls, tool results, and final text."""
        self.messages.append({"role": "user", "content": user_input})
        rounds = 0

        while True:
            response = self._call_llm()
            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            # ── Guardrail 检查（始终运行）──
            pending = [
                (tc.function.name, self._parse_args(tc.function.arguments))
                for tc in (msg.tool_calls or [])
            ]
            intervention = self._run_checks(rounds, pending)
            if intervention:
                yield {"type": "guardrail", "content": intervention}
                self.messages.append({"role": "user", "content": intervention})
                continue

            # ── 执行工具调用 ──
            if msg.tool_calls:
                rounds += 1
                for tool_call in msg.tool_calls:
                    name = tool_call.function.name
                    arguments = self._parse_args(tool_call.function.arguments)

                    yield {"type": "tool_call", "name": name,
                           "arguments": arguments, "id": tool_call.id}

                    result = self.tools.execute(name, arguments, self.store)
                    if len(result) > 8000:
                        result = result[:8000] + "\n... (truncated)"

                    yield {"type": "tool_result", "name": name,
                           "result": result, "id": tool_call.id}

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
                    self._tool_history.append((name, arguments, result))

            # 无工具调用 → 结束
            if not msg.tool_calls:
                yield {"type": "done", "content": msg.content or ""}
                return

    # ──────────────── 工具方法 ────────────────

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
