"""Pontis Agent - Interactive data analysis agent with tool calling."""
import json
import logging
import os
import sys
from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Tuple, Union

from openai import OpenAI

from storage.workspace import Workspace
from agent.utils import load_agent_config
from agent.config import default_spec
from agent.tools import build_registry
from agent.prompt import build_prompt
from agent.guardrail_api import Guardrail, CallVerdict, GuardrailContext

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

_MAX_TOOL_RESULT = 8000


# ═══════════════════════════════════════════════════════════
#  PontisAgent
# ═══════════════════════════════════════════════════════════

class PontusAgent:
    """Interactive agent that uses Pontis tools to analyze project data."""

    def __init__(self, project_path: str,
                 tools=None,
                 system_prompt: Optional[str] = None,
                 guardrails: Optional[List[Guardrail]] = None,
                 logger_name: Optional[str] = None,
                 trace_callback=None,
                 active_projects: Optional[List[str]] = None):
        self.project_path = project_path
        if not active_projects:
            active_projects = [os.path.basename(os.path.abspath(project_path))]
        self.workspace = Workspace(project_path=project_path,
                                   active_projects=active_projects)
        self.config = load_agent_config(project_path)
        self.logger = logging.getLogger(logger_name or __name__)
        self._trace_callback = trace_callback

        if not self.config["api_key"]:
            print("Error: No API key configured.")
            print("Set OPENAI_API_KEY env var, or create ~/.pontis/config.yml")
            sys.exit(1)

        self.client = OpenAI(
            api_key=self.config["api_key"],
            base_url=self.config["provider"],
            timeout=120.0,
        )

        self.tools = tools or build_registry(default_spec(project_path))
        self.system_prompt = system_prompt or build_prompt(default_spec(project_path))
        self.guardrails = guardrails or []
        self._tool_history: List[Tuple[str, dict, str]] = []
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self._empty_text_retries = 0

    # ──────────────── LLM 调用 ────────────────

    def _call_llm(self):
        tool_defs = self.tools.get_definitions()
        kwargs = {
            "model": self.config["model"],
            "messages": self.messages,
            "max_tokens": self.config.get("max_tokens", 8192),
        }
        if tool_defs:
            kwargs["tools"] = tool_defs
        if self.config.get("thinking", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.config.get("thinking_effort", "high")
        else:
            kwargs["temperature"] = self.config.get("temperature", 0.3)
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

    # ──────────────── 工具执行 ────────────────

    def _execute_tool(self, name: str, arguments: dict, tool_call_id: str) -> str:
        """执行单个工具调用：执行 → 截断 → 记录。"""
        result = self.tools.execute(name, arguments, self.workspace)

        if len(result) > _MAX_TOOL_RESULT:
            result = result[:_MAX_TOOL_RESULT] + "\n... (truncated)"

        self.logger.info(f"Tool result [{name}]:\n  " + "\n  ".join(result.split("\n")))
        self.messages.append({
            "role": "tool", "tool_call_id": tool_call_id,
            "content": result,
        })
        self._tool_history.append((name, arguments, result))

        return result

    # ──────────────── Guardrail 层 ────────────────

    def _collect_verdicts(self, ctx: GuardrailContext
                          ) -> Dict[Union[int, str], List[Tuple[str, CallVerdict]]]:
        """运行所有 guardrail，收集 per-call 裁决矩阵。"""
        verdicts: Dict[Union[int, str], List[Tuple[str, CallVerdict]]] = defaultdict(list)
        for g in self.guardrails:
            for key, v in g.check(ctx).items():
                verdicts[key].append((g.__class__.__name__, v))
        return dict(verdicts)

    @staticmethod
    def _aggregate(vs: List[Tuple[str, CallVerdict]]) -> Tuple[str, str, Optional[dict]]:
        """聚合单个调用的所有裁决。"""
        blocks = [(s, v) for s, v in vs if v.action == "block"]
        warnings = [(s, v) for s, v in vs if v.action == "warn"]

        merged_args: Optional[dict] = None
        for _, v in vs:
            if v.modified_args is not None:
                if merged_args is None:
                    merged_args = dict(v.modified_args)
                else:
                    merged_args.update(v.modified_args)

        if blocks:
            msg = "\n".join(f"[{s}] {v.message}" for s, v in blocks)
            return "block", msg, None

        if warnings:
            msg = "\n".join(f"[{s}] {v.message}" for s, v in warnings)
            return "warn", msg, merged_args

        return "allow", "", merged_args

    def _guardrail_process(self, ctx: GuardrailContext, msg,
                           tool_calls) -> Iterator[dict]:
        """guardrail 层：裁决 → 聚合 → 执行 → 后检查。"""
        verdicts = self._collect_verdicts(ctx)

        # ── 文本响应 ──
        if not tool_calls:
            text_vs = verdicts.get("text", [])
            action, message, _ = self._aggregate(text_vs)
            if action == "block":
                sources = "+".join(s for s, v in text_vs if v.action == "block")
                self.logger.info(f"Guardrail block [{sources}]: {message}")
                yield {"type": "blocked", "guardrail": sources, "content": message}
                self.messages.append({"role": "user", "content": message})
                return
            if not (msg.content or "").strip():
                self._empty_text_retries += 1
                if self._empty_text_retries >= 3:
                    self.logger.info("Empty assistant text response repeated; aborting empty loop")
                    yield {"type": "done", "content": ""}
                    return
                repair = "请直接给出最终文本响应；不要留空。若任务要求 SQL，就只输出最终 SQL 代码块。"
                self.logger.info("Empty assistant text response; reprompting")
                yield {"type": "warning", "guardrail": "EmptyTextResponse", "content": repair}
                self.messages.append({"role": "user", "content": repair})
                return
            self._empty_text_retries = 0
            if msg.content:
                self.logger.info(f"Agent done: {msg.content}")
            yield {"type": "done", "content": msg.content or ""}
            return

        # ── 工具调用：per-call 聚合 + 执行 ──
        deferred_warnings = []

        for i, tc in enumerate(tool_calls):
            name = tc.function.name
            call_vs = verdicts.get(i, [])
            action, message, modified_args = self._aggregate(call_vs)

            if action == "block":
                sources = "+".join(s for s, v in call_vs if v.action == "block")
                self.logger.info(f"Guardrail block [{sources}] call#{i}({name}): {message}")
                yield {"type": "blocked", "guardrail": sources,
                       "call_index": i, "name": name,
                       "arguments": self._parse_args(tc.function.arguments),
                       "content": message}
                self.messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": message,
                })
                continue

            args = modified_args or self._parse_args(tc.function.arguments)
            yield {"type": "tool_call", "name": name,
                   "arguments": args, "id": tc.id}
            result = self._execute_tool(name, args, tc.id)

            for g in self.guardrails:
                modified = g.post_check(ctx, i, name, args, result)
                if modified is not None:
                    result = modified
                    self.messages[-1]["content"] = result

            yield {"type": "tool_result", "name": name,
                   "result": result, "id": tc.id}

            if action == "warn":
                sources = "+".join(s for s, v in call_vs if v.action == "warn")
                self.logger.info(f"Guardrail warn [{sources}] call#{i}({name}): {message}")
                yield {"type": "warning", "guardrail": sources,
                       "call_index": i, "content": message}
                deferred_warnings.append(message)

        for wmsg in deferred_warnings:
            self.messages.append({"role": "user", "content": wmsg})

    # ──────────────── 核心循环 ────────────────

    def _run_loop(self, user_input: str) -> Iterator[dict]:
        """核心 agent 循环。"""
        self.messages.append({"role": "user", "content": user_input})
        rounds = 0

        while True:
            msg = self._call_llm_round()
            self._log_response(msg)

            pending = [
                (tc.function.name, self._parse_args(tc.function.arguments))
                for tc in (msg.tool_calls or [])
            ]
            ctx = GuardrailContext(
                messages=self.messages,
                tool_history=self._tool_history,
                workspace=self.workspace,
                rounds=rounds,
                pending_calls=pending,
            )

            done = False
            for event in self._guardrail_process(ctx, msg, msg.tool_calls):
                if self._trace_callback:
                    self._trace_callback(event)
                yield event
                if event["type"] == "done":
                    done = True

            if done:
                return

            rounds += 1

        yield {"type": "done", "content": ""}

    # ──────────────── 公开接口 ────────────────

    def chat(self, user_input: str) -> str:
        """Send user input and return the agent's final text response."""
        result = ""
        self._empty_text_retries = 0
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

    def _log_response(self, msg):
        if msg.tool_calls:
            for tc in msg.tool_calls:
                self.logger.info(f"Tool call: {tc.function.name}({tc.function.arguments or '{}'})")
        elif msg.content:
            self.logger.info(f"LLM text: {msg.content}")

    @staticmethod
    def _parse_args(args_str) -> dict:
        if args_str is None:
            return {}
        if isinstance(args_str, dict):
            return args_str
        if hasattr(args_str, "model_dump"):
            dumped = args_str.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        if hasattr(args_str, "to_dict"):
            dumped = args_str.to_dict()
            return dumped if isinstance(dumped, dict) else {}
        if not isinstance(args_str, str):
            return {}

        for loader in (json.loads, json.JSONDecoder(strict=False).decode):
            try:
                parsed = loader(args_str)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        repaired = []
        in_string = False
        escape = False
        for ch in args_str:
            if in_string:
                if escape:
                    repaired.append(ch)
                    escape = False
                    continue
                if ch == "\\":
                    repaired.append(ch)
                    escape = True
                    continue
                if ch == '"':
                    repaired.append(ch)
                    in_string = False
                    continue
                if ch == "\n":
                    repaired.append("\\n")
                    continue
                if ch == "\r":
                    repaired.append("\\r")
                    continue
                if ch == "\t":
                    repaired.append("\\t")
                    continue
                repaired.append(ch)
                continue
            repaired.append(ch)
            if ch == '"':
                in_string = True

        repaired_args = "".join(repaired)
        for loader in (json.loads, json.JSONDecoder(strict=False).decode):
            try:
                parsed = loader(repaired_args)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
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
