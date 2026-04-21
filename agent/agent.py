"""Pontis Agent - Interactive data analysis agent with tool calling."""
import json
import sys
from typing import Iterator, Optional

from openai import OpenAI

from storage import Store
from agent.config import load_agent_config
from agent.tools import ToolRegistry, build_readonly_registry
from agent.prompt import build_prompt


class PontisAgent:
    """Interactive agent that uses Pontis tools to analyze project data.

    Args:
        project_path: Path to the project directory.
        tools: ToolRegistry instance. Defaults to readonly registry.
        system_prompt: System prompt string. Defaults to readonly prompt.
    """

    def __init__(self, project_path: str,
                 tools: Optional[ToolRegistry] = None,
                 system_prompt: Optional[str] = None):
        self.project_path = project_path
        self.store = Store(project_path)
        self.config = load_agent_config(project_path)

        if not self.config.api_key:
            print("Error: No API key configured.")
            print("Set OPENAI_API_KEY env var, or create ~/.pontis/agent.yml with:")
            print("  llm_api_key: your-key-here")
            sys.exit(1)

        self.client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.provider,
        )

        # 可注入的工具和 prompt
        self.tools = tools or build_readonly_registry()
        self.system_prompt = system_prompt or build_prompt("readonly", project_path)
        self.messages = [
            {"role": "system", "content": self.system_prompt},
        ]

    def chat(self, user_input: str, max_rounds: int = 0) -> str:
        """Send user input and return the agent's response.

        Args:
            user_input: User message.
            max_rounds: Max tool-call rounds. 0 = unlimited (interactive mode).
        """
        self.messages.append({"role": "user", "content": user_input})

        rounds = 0
        # Loop: LLM may make multiple rounds of tool calls
        while True:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=self.messages,
                tools=self.tools.get_definitions(),
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )

            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            # If no tool calls, return the text response
            if not msg.tool_calls:
                return msg.content or ""

            rounds += 1
            if 0 < max_rounds <= rounds:
                return msg.content or "[max rounds reached]"

            # Execute all tool calls
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                print(f"  \033[90m[{name}({arguments})]\033[0m")
                result = self.tools.execute(name, arguments, self.store)

                # Truncate very long results to avoid context overflow
                if len(result) > 8000:
                    result = result[:8000] + "\n... (truncated)"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

    def chat_stream(self, user_input: str) -> Iterator[dict]:
        """Stream chat events: tool calls, tool results, and final text.

        Yields dicts with:
          {"type": "tool_call", "name": ..., "arguments": ..., "id": ...}
          {"type": "tool_result", "name": ..., "result": ..., "id": ...}
          {"type": "done", "content": ...}
        """
        self.messages.append({"role": "user", "content": user_input})

        while True:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=self.messages,
                tools=self.tools.get_definitions(),
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )

            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            if not msg.tool_calls:
                yield {"type": "done", "content": msg.content or ""}
                return

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

    def reset_conversation(self):
        """Clear conversation history (keep system prompt) for a fresh session."""
        self.messages = [self.messages[0]]

    def run(self):
        """Run the interactive REPL."""
        print(f"\n\033[1mPontis Agent\033[0m — {self.project_path}")
        print(f"Model: {self.config.model}")
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
