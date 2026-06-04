"""Guardrail 框架级 API — 类型定义和基类。

与业务逻辑完全分离，适用于任何 agent 框架。

聚合规则：
  - 每个 tool call 独立聚合所有 guardrail 的裁决
  - 任一 guardrail block → 该调用被拦截
  - 多个 block → 消息聚合
  - 无 block 但有 warn → 执行 + 聚合警告
  - 全 allow → 正常执行
"""
from abc import ABC
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union


@dataclass
class CallVerdict:
    """单个 guardrail 对单个调用的裁决。

    action:
      - "allow"  → 放行
      - "block"  → 拦截该调用
      - "warn"   → 放行但追加提醒
    """
    action: str = "allow"
    message: str = ""
    modified_args: Optional[dict] = None
    replace_messages: Optional[List[dict]] = None
    replace_tool_history: Optional[List[Tuple[str, dict, str]]] = None


@dataclass
class PostToolAction:
    """工具执行后的同步动作。

    replace_result:
      显式替换当前 tool_result。默认不替换。

    append_messages:
      追加给主 agent 的独立消息。适合 nudge、attachment、同步 hook 结果。

    trace_messages:
      仅写入事件流/日志，不追加给主 agent。适合记录异步 fork 启动等
      不应改变模型行为的框架事件。
    """
    replace_result: Optional[str] = None
    append_messages: List[str] = field(default_factory=list)
    trace_messages: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            self.replace_result is None
            and not self.append_messages
            and not self.trace_messages
        )


class GuardrailContext:
    """Guardrail 最小 API — 框架级，无业务逻辑。"""

    def __init__(self, *, messages: list, tool_history: list,
                 workspace, rounds: int,
                 pending_calls: List[Tuple[str, dict]],
                 agent=None,
                 current_response: Optional[str] = None):
        self._messages = messages
        self._tool_history = tool_history
        self._workspace = workspace
        self.rounds = rounds
        self.pending_calls = pending_calls
        self.agent = agent
        self._current_response = current_response

    # ── 类型判断 ──

    def is_tool_call(self) -> bool:
        """LLM 返回了 tool_calls？"""
        return bool(self.pending_calls)

    def is_text_response(self) -> bool:
        """LLM 返回了文本？"""
        return not self.pending_calls

    # ── 上一条消息 ──

    @property
    def last_response(self) -> Optional[str]:
        """上一条 LLM 文本内容。"""
        if self._current_response:
            return self._current_response
        for msg in reversed(self._messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return None

    # ── 全局历史 ──

    @property
    def messages(self) -> List[dict]:
        """完整对话历史。"""
        return self._messages

    @property
    def tool_history(self) -> List[Tuple[str, dict, str]]:
        """历史工具调用: [(name, args, result), ...]"""
        return self._tool_history

    @property
    def workspace(self):
        """Workspace 实例。"""
        return self._workspace


class Guardrail(ABC):
    """Guardrail 基类 — 框架级接口，无业务逻辑。

    check() 返回 {key: CallVerdict} 字典：
      - key = int (call_index) → 针对特定工具调用
      - key = "text" → 针对文本响应
      - {} → 全部放行

    多个 guardrail 对同一个调用返回 verdict 时，由框架聚合：
      block 优先，消息聚合；无 block 时 warn 聚合。
    """

    def check(self, ctx: GuardrailContext) -> Dict[Union[int, str], CallVerdict]:
        """检查当前 LLM 响应，返回 per-call 裁决。"""
        return {}

    def post_tool(self, ctx: GuardrailContext,
                  call_index: int, name: str, args: dict,
                  result: str) -> Optional[PostToolAction]:
        """工具执行后：观察结果、追加消息、显式替换结果或启动后台任务。"""
        return None

    def drain_ready(self, ctx: GuardrailContext) -> List[str]:
        """返回已准备好的异步补充消息。

        该 hook 用于非阻塞 sidechain/prefetch：post_tool 可以启动后台任务，
        drain_ready 只消费已经完成的结果，不应阻塞主 agent。
        """
        return []
