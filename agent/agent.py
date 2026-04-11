"""Pontis Agent - Interactive data analysis agent with tool calling."""
import json
import sys

from openai import OpenAI

from storage import ProjectStore
from agent.config import load_agent_config
from agent.tools import TOOL_DEFINITIONS, execute_tool
from agent.system_prompt import build_system_prompt


class PontisAgent:
    """Interactive agent that uses Pontis tools to analyze project data."""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.store = ProjectStore(project_path)
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

        self.system_prompt = build_system_prompt(project_path)
        self.messages = [
            {"role": "system", "content": self.system_prompt},
        ]

    def chat(self, user_input: str) -> str:
        """Send user input and return the agent's response."""
        self.messages.append({"role": "user", "content": user_input})

        # Loop: LLM may make multiple rounds of tool calls (no limit)
        while True:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=self.messages,
                tools=TOOL_DEFINITIONS,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )

            msg = response.choices[0].message
            self.messages.append(msg.to_dict())

            # If no tool calls, return the text response
            if not msg.tool_calls:
                return msg.content or ""

            # Execute all tool calls
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                print(f"  \033[90m[{name}({arguments})]\033[0m")
                result = execute_tool(name, arguments, self.store)

                # Truncate very long results to avoid context overflow
                if len(result) > 8000:
                    result = result[:8000] + "\n... (truncated)"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

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
