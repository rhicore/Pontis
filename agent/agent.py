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
from agent.prompt import build_prompt_messages
from agent.guardrail_api import Guardrail, CallVerdict, GuardrailContext, PostToolAction
from agent.runtime_metrics import (
    estimate_messages_tokens,
    estimate_tokens,
    merge_cache_accounting_sources,
    normalize_cache_accounting,
    serialize_request,
    split_prompt_tokens,
)

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
                 system_prompt: Optional[Union[str, List[str]]] = None,
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
        if not system_prompt:
            system_prompt = build_prompt_messages(default_spec(project_path))
        self.system_prompt = system_prompt
        self.guardrails = guardrails or []
        self._tool_history: List[Tuple[str, dict, str]] = []
        self._system_messages = self._normalize_system_prompt(self.system_prompt)
        self.messages = list(self._system_messages)
        self._empty_text_retries = 0
        self._llm_rounds = 0
        self._input_tokens = 0
        self._pre_input_tokens = 0
        self._runtime_input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._cache_hit_input_tokens = 0
        self._cache_miss_input_tokens = 0
        self._cache_unknown_input_tokens = 0
        self._fresh_input_tokens = 0
        self._cache_accounting_sources: List[str] = []
        self._previous_prompt_text: Optional[str] = None

    # ──────────────── LLM 调用 ────────────────

    def _call_llm(self):
        tool_defs = self.tools.get_definitions()
        kwargs = {
            "model": self.config["model"],
            "messages": self.messages,
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
        static_prompt_tokens = self._static_prompt_tokens()
        prompt_text = serialize_request(self.messages, self.tools.get_definitions())
        response = self._call_llm()
        self._record_llm_usage(
            response,
            static_prompt_tokens=static_prompt_tokens,
            prompt_text=prompt_text,
        )
        self._previous_prompt_text = prompt_text
        msg = response.choices[0].message
        self.messages.append(self._msg_to_dict(msg))
        return msg

    def _static_prompt_tokens(self) -> int:
        return estimate_messages_tokens(self._system_messages) + estimate_tokens(self.tools.get_definitions())

    def _record_llm_usage(self, response, *, static_prompt_tokens: int = 0, prompt_text: str | None = None) -> None:
        self._llm_rounds += 1
        usage = getattr(response, "usage", None)
        if not usage:
            return
        input_tokens = (
            getattr(usage, "prompt_tokens", None)
            if getattr(usage, "prompt_tokens", None) is not None
            else getattr(usage, "input_tokens", 0)
        )
        output_tokens = (
            getattr(usage, "completion_tokens", None)
            if getattr(usage, "completion_tokens", None) is not None
            else getattr(usage, "output_tokens", 0)
        )
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is None:
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
        split = split_prompt_tokens(int(input_tokens or 0), static_prompt_tokens)
        cache = normalize_cache_accounting(
            usage=usage,
            input_tokens=int(input_tokens or 0),
            static_prompt_tokens=static_prompt_tokens,
            current_prompt=prompt_text,
            previous_prompt=self._previous_prompt_text,
        )
        self._input_tokens += int(input_tokens or 0)
        self._pre_input_tokens += split["pre_input_tokens"]
        self._runtime_input_tokens += split["runtime_input_tokens"]
        self._output_tokens += int(output_tokens or 0)
        self._total_tokens += int(total_tokens or 0)
        self._cache_hit_input_tokens += cache["cache_hit_input_tokens"]
        self._cache_miss_input_tokens += cache["cache_miss_input_tokens"]
        self._cache_unknown_input_tokens += cache["cache_unknown_input_tokens"]
        self._fresh_input_tokens += cache["fresh_input_tokens"]
        self._cache_accounting_sources.append(cache["cache_accounting_source"])

    def llm_metrics(self) -> dict:
        return {
            "llm_rounds": self._llm_rounds,
            "input_tokens": self._input_tokens,
            "pre_input_tokens": self._pre_input_tokens,
            "runtime_input_tokens": self._runtime_input_tokens,
            "runtime_output_tokens": self._output_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._total_tokens,
            "cached_input_tokens": self._cache_hit_input_tokens,
            "uncached_input_tokens": self._cache_miss_input_tokens + self._cache_unknown_input_tokens,
            "cache_hit_input_tokens": self._cache_hit_input_tokens,
            "cache_miss_input_tokens": self._cache_miss_input_tokens,
            "cache_unknown_input_tokens": self._cache_unknown_input_tokens,
            "fresh_input_tokens": self._fresh_input_tokens,
            "cache_accounting_source": merge_cache_accounting_sources(self._cache_accounting_sources),
        }

    def _msg_to_dict(self, msg) -> dict:
        d = msg.to_dict()
        if self.config.get("thinking") and msg.tool_calls:
            rc = getattr(msg, "reasoning_content", None)
            if rc:
                d["reasoning_content"] = rc
        return d

    @staticmethod
    def _normalize_system_prompt(system_prompt: Union[str, List[str]]) -> List[dict]:
        if isinstance(system_prompt, str):
            parts = [system_prompt]
        else:
            parts = [str(part) for part in system_prompt if str(part).strip()]
        return [{"role": "system", "content": part} for part in parts]

    def set_system_prompt(self, system_prompt: Union[str, List[str]]) -> None:
        """Replace system messages while preserving non-system conversation."""
        self.system_prompt = system_prompt
        self._system_messages = self._normalize_system_prompt(system_prompt)
        idx = 0
        while idx < len(self.messages) and self.messages[idx].get("role") == "system":
            idx += 1
        self.messages = list(self._system_messages) + self.messages[idx:]

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

    def _drain_guardrail_messages(self, rounds: int) -> Iterator[dict]:
        """消费非阻塞 guardrail sidechain 已完成的补充消息。"""
        ctx = GuardrailContext(
            messages=self.messages,
            tool_history=self._tool_history,
            workspace=self.workspace,
            rounds=rounds,
            pending_calls=[],
            agent=self,
        )
        for g in self.guardrails:
            for content in g.drain_ready(ctx):
                if not content:
                    continue
                source = g.__class__.__name__
                self.logger.info(f"Guardrail sidechain [{source}]:\n  " + "\n  ".join(content.split("\n")))
                self.messages.append({"role": "user", "content": content})
                yield {"type": "sidechain", "guardrail": source, "content": content}

    def _apply_post_tool_action(self, action: Optional[PostToolAction],
                                result: str) -> Tuple[str, List[str], List[str]]:
        appended = []
        trace_messages = []
        if action is None or action.is_empty():
            return result, appended, trace_messages
        if action.replace_result is not None:
            result = action.replace_result
            self.messages[-1]["content"] = result
        for content in action.append_messages:
            if not content:
                continue
            self.messages.append({"role": "user", "content": content})
            appended.append(content)
        for content in action.trace_messages:
            if content:
                trace_messages.append(content)
        return result, appended, trace_messages

    @staticmethod
    def _aggregate(vs: List[Tuple[str, CallVerdict]]
                   ) -> Tuple[str, str, Optional[dict], Optional[list], Optional[list]]:
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
            replace_messages = next((v.replace_messages for _, v in blocks if v.replace_messages is not None), None)
            replace_tool_history = next(
                (v.replace_tool_history for _, v in blocks if v.replace_tool_history is not None),
                None,
            )
            return "block", msg, None, replace_messages, replace_tool_history

        if warnings:
            msg = "\n".join(f"[{s}] {v.message}" for s, v in warnings)
            return "warn", msg, merged_args, None, None

        return "allow", "", merged_args, None, None

    def _guardrail_process(self, ctx: GuardrailContext, msg,
                           tool_calls) -> Iterator[dict]:
        """guardrail 层：裁决 → 聚合 → 执行 → 后检查。"""
        verdicts = self._collect_verdicts(ctx)

        # ── 文本响应 ──
        if not tool_calls:
            text_vs = verdicts.get("text", [])
            action, message, _, replace_messages, replace_tool_history = self._aggregate(text_vs)
            if action == "block":
                sources = "+".join(s for s, v in text_vs if v.action == "block")
                self.logger.info(f"Guardrail block [{sources}]: {message}")
                yield {"type": "blocked", "guardrail": sources, "content": message}
                if replace_messages is not None:
                    self.messages = list(replace_messages)
                    self.logger.info(
                        "Guardrail context rewrite [%s]: %d messages",
                        sources,
                        len(self.messages),
                    )
                    yield {
                        "type": "context_rewrite",
                        "guardrail": sources,
                        "message_count": len(self.messages),
                    }
                else:
                    self.messages.append({"role": "user", "content": message})
                if replace_tool_history is not None:
                    self._tool_history = list(replace_tool_history)
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
            action, message, modified_args, _, _ = self._aggregate(call_vs)

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

            parsed_args, parse_error = self._parse_args_or_error(tc.function.arguments)
            if parse_error:
                content = (
                    "Tool argument parse error: "
                    f"{parse_error}. Retry this tool call with valid JSON arguments. "
                    "For long text fields, preserve the target ref and fields exactly."
                )
                self.logger.info(f"Tool argument parse error call#{i}({name}): {parse_error}")
                yield {
                    "type": "tool_call",
                    "name": name,
                    "arguments": {"__parse_error": parse_error},
                    "id": tc.id,
                }
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })
                yield {"type": "tool_result", "name": name,
                       "result": content, "id": tc.id}
                continue

            args = modified_args or parsed_args
            yield {"type": "tool_call", "name": name,
                   "arguments": args, "id": tc.id}
            result = self._execute_tool(name, args, tc.id)

            for g in self.guardrails:
                action_result = g.post_tool(ctx, i, name, args, result)
                result, appended, trace_messages = self._apply_post_tool_action(action_result, result)
                for content in appended:
                    source = g.__class__.__name__
                    self.logger.info(f"Guardrail append [{source}] call#{i}({name}): {content}")
                    yield {"type": "append", "guardrail": source,
                           "call_index": i, "content": content}
                for content in trace_messages:
                    source = g.__class__.__name__
                    self.logger.info(f"Guardrail trace [{source}] call#{i}({name}): {content}")
                    yield {"type": "trace", "guardrail": source,
                           "call_index": i, "content": content,
                           "trace_only": True}

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

        for event in self._drain_guardrail_messages(ctx.rounds):
            yield event

    # ──────────────── 核心循环 ────────────────

    def _run_loop(self, user_input: str) -> Iterator[dict]:
        """核心 agent 循环。"""
        self.messages.append({"role": "user", "content": user_input})
        rounds = 0

        while True:
            for event in self._drain_guardrail_messages(rounds):
                if self._trace_callback:
                    self._trace_callback(event)
                yield event

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
                agent=self,
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
        parsed, _ = PontusAgent._parse_args_or_error(args_str)
        return parsed

    @staticmethod
    def _parse_args_or_error(args_str) -> tuple[dict, str | None]:
        if args_str is None:
            return {}, None
        if isinstance(args_str, dict):
            return args_str, None
        if hasattr(args_str, "model_dump"):
            dumped = args_str.model_dump()
            return (dumped, None) if isinstance(dumped, dict) else ({}, "arguments object is not a JSON object")
        if hasattr(args_str, "to_dict"):
            dumped = args_str.to_dict()
            return (dumped, None) if isinstance(dumped, dict) else ({}, "arguments object is not a JSON object")
        if not isinstance(args_str, str):
            return {}, "arguments are not a string or object"

        last_error = None
        for loader in (json.loads, json.JSONDecoder(strict=False).decode):
            try:
                parsed = loader(args_str)
                return (parsed, None) if isinstance(parsed, dict) else ({}, "arguments JSON is not an object")
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc

        repaired = []
        in_string = False
        string_is_key = False
        escape = False
        stack = []
        for idx, ch in enumerate(args_str):
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
                    nxt = PontusAgent._next_nonspace(args_str, idx + 1)
                    if (string_is_key and nxt == ":") or (not string_is_key and nxt in {",", "}", "]", None}):
                        repaired.append(ch)
                        in_string = False
                    else:
                        repaired.append('\\"')
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
                string_is_key = bool(stack and stack[-1] in {"object_key", "object_key_or_end"})
            elif ch == "{":
                stack.append("object_key_or_end")
            elif ch == "[":
                stack.append("array_value_or_end")
            elif ch == ":" and stack and stack[-1] in {"object_key", "object_key_or_end", "object_colon"}:
                stack[-1] = "object_value"
            elif ch == "," and stack:
                if stack[-1].startswith("object_"):
                    stack[-1] = "object_key"
                elif stack[-1].startswith("array_"):
                    stack[-1] = "array_value"
            elif ch == "}" and stack and stack[-1].startswith("object_"):
                stack.pop()
            elif ch == "]" and stack and stack[-1].startswith("array_"):
                stack.pop()

        repaired_args = "".join(repaired)
        for loader in (json.loads, json.JSONDecoder(strict=False).decode):
            try:
                parsed = loader(repaired_args)
                return (parsed, None) if isinstance(parsed, dict) else ({}, "repaired arguments JSON is not an object")
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc

        balanced_args = PontusAgent._append_missing_json_closers(repaired_args)
        if balanced_args != repaired_args:
            for loader in (json.loads, json.JSONDecoder(strict=False).decode):
                try:
                    parsed = loader(balanced_args)
                    return (parsed, None) if isinstance(parsed, dict) else ({}, "balanced arguments JSON is not an object")
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    last_error = exc
        if last_error:
            return {}, f"invalid JSON arguments ({last_error})"
        return {}, "invalid JSON arguments"

    @staticmethod
    def _next_nonspace(text: str, start: int) -> str | None:
        for ch in text[start:]:
            if not ch.isspace():
                return ch
        return None

    @staticmethod
    def _append_missing_json_closers(text: str) -> str:
        stack = []
        in_string = False
        escape = False
        for ch in text:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if not stack or stack[-1] != ch:
                    return text
                stack.pop()
        suffix = ""
        if in_string:
            if escape:
                suffix += "\\"
            suffix += '"'
        if stack:
            suffix += "".join(reversed(stack))
        if not suffix:
            return text
        return text + suffix

    def reset_conversation(self):
        """Clear conversation history (keep system prompt) for a fresh session."""
        self.messages = list(self._system_messages)

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
