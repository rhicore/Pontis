"""Pontis Agent - Interactive data analysis agent with tool calling."""
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Iterator, Optional

from openai import OpenAI

from storage import Store
from agent.config import load_agent_config
from agent.tools import ToolRegistry, build_readonly_registry, build_writer_registry
from agent.prompt import build_prompt
from agent.prompt._effort import get_effort_max_rounds, VALID_EFFORTS

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

_MAX_ROUNDS_STOP_PROMPT = """\
⚠️ 已达到工具调用上限（{max_rounds} 轮）。请不要再调用任何工具。

基于你目前已掌握的信息，直接完成你的任务：
- 如果你正在生成 SQL，直接输出你当前最佳理解的 SQL
- 如果你正在分析数据，总结你已发现的内容
- 如果你正在写总结，直接输出已有的总结内容

不要解释为什么停止，直接输出结果。"""


# ═══════════════════════════════════════════════════════════
#  Agent 创建配置
# ═══════════════════════════════════════════════════════════

@dataclass
class AgentSpec:
    """Agent 创建参数 — 调用方只需填这个，工厂自动组装 prompt/tools/max_rounds。

    后续可扩展：model override、temperature override、custom tools 等。
    """
    mode: str = "readonly"          # readonly | writer | sub_agent | benchmark
    effort: str = "mid"             # low | mid | high
    debug: bool = False
    max_rounds: Optional[int] = None  # None = 按模式默认（readonly 用 effort 推导，writer 无限制）


def create_agent(project_path: str, spec: AgentSpec = None) -> "PontisAgent":
    """工厂：根据 spec 自动组装 prompt + tools + max_rounds，返回就绪的 agent。"""
    if spec is None:
        spec = AgentSpec()

    # 1. prompt
    prompt = build_prompt(spec.mode, project_path, effort=spec.effort, debug=spec.debug)

    # 2. tools
    if spec.mode in ("writer", "sub_agent"):
        tools = build_writer_registry()
    else:
        tools = build_readonly_registry()

    # 3. max_rounds：
    #    - 显式指定 → 直接用
    #    - readonly/benchmark/sub_agent → 从 effort 推导
    #    - writer → 无限制（None）
    if spec.max_rounds is not None:
        max_rounds = spec.max_rounds
    elif spec.mode == "writer":
        max_rounds = None
    else:
        max_rounds = get_effort_max_rounds(spec.effort)

    agent = PontisAgent(project_path, tools=tools, system_prompt=prompt)
    agent.max_rounds = max_rounds
    return agent


# ═══════════════════════════════════════════════════════════
#  PontisAgent
# ═══════════════════════════════════════════════════════════

class PontisAgent:
    """Interactive agent that uses Pontis tools to analyze project data."""

    def __init__(self, project_path: str,
                 tools: Optional[ToolRegistry] = None,
                 system_prompt: Optional[str] = None):
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

        self.tools = tools or build_readonly_registry()
        self.system_prompt = system_prompt or build_prompt("readonly", project_path)
        self.max_rounds: Optional[int] = None  # 由 create_agent 设置
        self.messages = [
            {"role": "system", "content": self.system_prompt},
        ]

    def chat(self, user_input: str, max_rounds: Optional[int] = None) -> str:
        """Send user input and return the agent's response.

        max_rounds 优先级：参数 > self.max_rounds（由 effort 绑定）> 无限制
        """
        effective_max = max_rounds or self.max_rounds

        self.messages.append({"role": "user", "content": user_input})

        rounds = 0
        while True:
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=self.messages,
                tools=self.tools.get_definitions(),
                max_tokens=self.config["max_tokens"],
                temperature=self.config["temperature"],
            )

            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            # No tool calls → agent finished
            if not msg.tool_calls:
                if msg.content:
                    logger.info(f"Agent done: {msg.content}")
                return msg.content or ""

            rounds += 1
            logger.debug(f"Round {rounds}")

            # Execute all tool calls
            for tool_call in msg.tool_calls:
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

            # Max rounds reached → inject stop prompt and get final output
            if effective_max and rounds >= effective_max:
                logger.info(f"Max rounds reached ({rounds}/{effective_max}), requesting final output")
                self.messages.append({
                    "role": "user",
                    "content": _MAX_ROUNDS_STOP_PROMPT.format(max_rounds=effective_max),
                })
                final_response = self.client.chat.completions.create(
                    model=self.config["model"],
                    messages=self.messages,
                    tools=self.tools.get_definitions(),
                    max_tokens=self.config["max_tokens"],
                    temperature=self.config["temperature"],
                )
                final_msg = final_response.choices[0].message
                self.messages.append(final_msg.to_dict())
                if final_msg.content:
                    logger.info(f"Agent done (max_rounds): {final_msg.content}")
                return final_msg.content or ""

    def chat_stream(self, user_input: str, max_rounds: Optional[int] = None) -> Iterator[dict]:
        """Stream chat events: tool calls, tool results, and final text."""
        effective_max = max_rounds or self.max_rounds

        self.messages.append({"role": "user", "content": user_input})

        rounds = 0
        while True:
            response = self.client.chat.completions.create(
                model=self.config["model"],
                messages=self.messages,
                tools=self.tools.get_definitions(),
                max_tokens=self.config["max_tokens"],
                temperature=self.config["temperature"],
            )

            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            if not msg.tool_calls:
                yield {"type": "done", "content": msg.content or ""}
                return

            rounds += 1

            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                yield {
                    "type": "tool_call",
                    "name": name,
                    "arguments": arguments,
                    "id": tool_call.id,
                }

                result = self.tools.execute(name, arguments, self.store)
                if len(result) > 8000:
                    result = result[:8000] + "\n... (truncated)"

                yield {
                    "type": "tool_result",
                    "name": name,
                    "result": result,
                    "id": tool_call.id,
                }

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            if effective_max and rounds >= effective_max:
                logger.info(f"Max rounds reached ({rounds}/{effective_max}), requesting final output")
                self.messages.append({
                    "role": "user",
                    "content": _MAX_ROUNDS_STOP_PROMPT.format(max_rounds=effective_max),
                })
                final_response = self.client.chat.completions.create(
                    model=self.config["model"],
                    messages=self.messages,
                    tools=self.tools.get_definitions(),
                    max_tokens=self.config["max_tokens"],
                    temperature=self.config["temperature"],
                )
                final_msg = final_response.choices[0].message
                self.messages.append(final_msg.to_dict())
                yield {"type": "done", "content": final_msg.content or ""}
                return

    def reset_conversation(self):
        """Clear conversation history (keep system prompt) for a fresh session."""
        self.messages = [self.messages[0]]

    def run(self):
        """Run the interactive REPL."""
        print(f"\n\033[1mPontis Agent\033[0m — {self.project_path}")
        print(f"Model: {self.config['model']}")
        if self.max_rounds:
            print(f"Max rounds: {self.max_rounds}")
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
