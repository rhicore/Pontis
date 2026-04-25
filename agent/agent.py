"""Pontis Agent - Interactive data analysis agent with tool calling."""
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple, Union

from openai import OpenAI

from storage import Store
from agent.utils import load_agent_config
from agent.tools import build_registry
from agent.prompt import build_prompt
from agent.guardrail_api import Guardrail, CallVerdict, GuardrailContext
from agent.guardrail import build_guardrails
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


def create_agent(project_path: str, spec: AgentSpec = None,
                 logger_name: Optional[str] = None) -> "PontisAgent":
    """工厂：根据 spec 自动组装 prompt + tools + guardrails。"""
    if spec is None:
        spec = AgentSpec()
    spec.project_path = project_path

    prompt = build_prompt(spec)
    tools = build_registry(spec)
    if not spec.guardrails:
        spec.guardrails = build_guardrails(spec)

    return PontisAgent(project_path, tools=tools, system_prompt=prompt,
                       guardrails=spec.guardrails, logger_name=logger_name)


# ═══════════════════════════════════════════════════════════
#  PontisAgent
# ═══════════════════════════════════════════════════════════

class PontisAgent:
    """Interactive agent that uses Pontis tools to analyze project data."""

    def __init__(self, project_path: str,
                 tools=None,
                 system_prompt: Optional[str] = None,
                 guardrails: Optional[List[Guardrail]] = None,
                 logger_name: Optional[str] = None):
        self.project_path = project_path
        self.store = Store(project_path)
        self.config = load_agent_config(project_path)
        self.logger = logging.getLogger(logger_name or __name__)

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

    # ──────────────── 工具执行 ────────────────

    def _execute_tool(self, name: str, arguments: dict, tool_call_id: str) -> str:
        """执行单个工具调用：执行 → 截断 → 记录。"""
        result = self.tools.execute(name, arguments, self.store)

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
    #
    # guardrail 层包裹除 LLM 调用外的所有逻辑：
    #   1. 收集所有 guardrail 对每个调用的裁决（多对多矩阵）
    #   2. 每个 tool call 独立聚合：block 优先，消息合并
    #   3. 执行允许的调用
    #   4. post_check 修改结果
    #   5. 文本响应也可被拦截
    #

    def _collect_verdicts(self, ctx: GuardrailContext
                          ) -> Dict[Union[int, str], List[Tuple[str, CallVerdict]]]:
        """运行所有 guardrail，收集 per-call 裁决矩阵。

        Returns: {call_index|"text": [(guardrail_name, CallVerdict), ...]}
        """
        verdicts: Dict[Union[int, str], List[Tuple[str, CallVerdict]]] = defaultdict(list)
        for g in self.guardrails:
            for key, v in g.check(ctx).items():
                verdicts[key].append((g.__class__.__name__, v))
        return dict(verdicts)

    @staticmethod
    def _aggregate(vs: List[Tuple[str, CallVerdict]]) -> Tuple[str, str, Optional[dict]]:
        """聚合单个调用的所有裁决。

        Returns: (action, aggregated_message, modified_args)
          - action: "block" | "warn" | "allow"
          - message: 聚合后的 block/warn 消息
          - modified_args: merge 所有 non-None modified_args（后注册覆盖同名 key）
        """
        blocks = [(s, v) for s, v in vs if v.action == "block"]
        warnings = [(s, v) for s, v in vs if v.action == "warn"]

        # merge modified_args: 按注册顺序，后注册的覆盖同名 key
        merged_args: Optional[dict] = None
        for _, v in vs:
            if v.modified_args is not None:
                if merged_args is None:
                    merged_args = dict(v.modified_args)
                else:
                    merged_args.update(v.modified_args)

        if blocks:
            msg = "\n".join(f"[{s}] {v.message}" for s, v in blocks)
            return "block", msg, None  # block 时 modified_args 无意义

        if warnings:
            msg = "\n".join(f"[{s}] {v.message}" for s, v in warnings)
            return "warn", msg, merged_args

        return "allow", "", merged_args

    def _guardrail_process(self, ctx: GuardrailContext, msg,
                           tool_calls) -> Iterator[dict]:
        """guardrail 层：裁决 → 聚合 → 执行 → 后检查。

        处理除 LLM 调用外的所有框架逻辑。
        yield 事件，调用者根据事件类型决定是否继续循环。
        """
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
            if msg.content:
                self.logger.info(f"Agent done: {msg.content}")
            yield {"type": "done", "content": msg.content or ""}
            return

        # ── 工具调用：per-call 聚合 + 执行 ──
        for i, tc in enumerate(tool_calls):
            name = tc.function.name
            call_vs = verdicts.get(i, [])
            action, message, modified_args = self._aggregate(call_vs)

            if action == "block":
                # 拦截：聚合所有 block guardrail 的消息
                sources = "+".join(s for s, v in call_vs if v.action == "block")
                self.logger.info(f"Guardrail block [{sources}] call#{i}({name}): {message}")
                yield {"type": "blocked", "guardrail": sources,
                       "call_index": i, "content": message}
                self.messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": message,
                })
                continue

            # 执行（可能用修改后的参数）
            args = modified_args or self._parse_args(tc.function.arguments)
            yield {"type": "tool_call", "name": name,
                   "arguments": args, "id": tc.id}
            result = self._execute_tool(name, args, tc.id)

            # Post-check: pipeline，每个 guardrail 依次处理前一个的输出
            for g in self.guardrails:
                modified = g.post_check(ctx, i, name, args, result)
                if modified is not None:
                    result = modified
                    self.messages[-1]["content"] = result

            yield {"type": "tool_result", "name": name,
                   "result": result, "id": tc.id}

            # 警告（执行后追加）
            if action == "warn":
                sources = "+".join(s for s, v in call_vs if v.action == "warn")
                self.logger.info(f"Guardrail warn [{sources}] call#{i}({name}): {message}")
                yield {"type": "warning", "guardrail": sources,
                       "call_index": i, "content": message}
                self.messages.append({"role": "user", "content": message})

    # ──────────────── 核心循环 ────────────────

    def _run_loop(self, user_input: str) -> Iterator[dict]:
        """核心 agent 循环。

        每轮只做两件事：调用 LLM → 交给 guardrail 层处理。
        guardrail 层包裹所有除 LLM 调用外的逻辑：
          裁决矩阵 → per-call 聚合 → 执行 → 后检查 → 警告。
        """
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
                store=self.store,
                rounds=rounds,
                pending_calls=pending,
            )

            done = False
            for event in self._guardrail_process(ctx, msg, msg.tool_calls):
                yield event
                if event["type"] == "done":
                    done = True

            if done:
                return

            rounds += 1

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

    def _log_response(self, msg):
        if msg.tool_calls:
            for tc in msg.tool_calls:
                self.logger.info(f"Tool call: {tc.function.name}({tc.function.arguments or '{}'})")
        elif msg.content:
            self.logger.info(f"LLM text: {msg.content}")

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
