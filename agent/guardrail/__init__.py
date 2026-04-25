"""Guardrail — 统一的 agent 行为监控框架。

每次 LLM 响应后（tool_calls 或 text），统一运行 guardrail 检查。
Guardrail 是纯粹的 supervisor：观测状态 → 决定是否干预 → 打回让模型重新思考。

新增模块只需：
  1. 在 guardrail/ 下新建文件，继承 Guardrail
  2. 实现 check(state, pending_calls) 方法
  3. 在 build_guardrails() 中注册
"""
import json
from abc import ABC
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


@dataclass
class AgentState:
    """Guardrail 可见的只读状态快照。"""
    messages: list                                           # 完整对话历史
    rounds: int                                              # 累计工具调用轮次
    tool_history: List[Tuple[str, dict, str]] = field(default_factory=list)
    store: object = None                                     # Store 实例（只读）


def _get_db_prefix(state: "AgentState", pending_calls: list) -> str:
    """从 query 工具调用的 file 参数提取数据库文件前缀。

    Returns: "formula_1.sqlite::" 或 ""
    """
    for name, args in pending_calls:
        if name == "query":
            f = args.get("file", "")
            if f:
                return f + "::"
    for msg in state.messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "query":
                try:
                    args = json.loads(tc["function"]["arguments"])
                    f = args.get("file", "")
                    if f:
                        return f + "::"
                except (json.JSONDecodeError, KeyError):
                    pass
    return ""


class Guardrail(ABC):
    """监控器基类。"""

    blocking: bool = True  # True = 阻止工具执行，False = 执行后追加提醒

    def check(self, state: AgentState, pending_calls: List[Tuple[str, dict]]) -> Optional[str]:
        """检查 agent 状态，决定是否干预。

        Args:
            state: 当前 agent 状态
            pending_calls: 即将执行的工具调用 [(name, arguments), ...]
                           文本响应时为空列表

        Returns:
            干预文本则阻止执行，让 LLM 重新思考；None 放行。
        """
        return None


def build_guardrails(spec) -> List[Guardrail]:
    """根据 AgentSpec 构建 guardrail 列表。"""
    from agent.guardrail.round_limit import RoundLimit
    from agent.guardrail.tool_abuse import ToolAbuse
    from agent.guardrail.sql_check import SQLEntityCheck
    from agent.guardrail.sql_join_check import BridgeTableCheck
    from agent.guardrail.sql_disambig_check import SQLDisambigCheck

    guardrails = []

    # 轮次上限
    from agent.prompt._effort import get_effort_max_rounds
    if spec.mode == "writer":
        max_rounds = spec.max_rounds
    else:
        max_rounds = spec.max_rounds or get_effort_max_rounds(spec.effort)
    if max_rounds:
        guardrails.append(RoundLimit(max_rounds))

    # query 连续调用检测
    guardrails.append(ToolAbuse("query", consecutive_limit=3))

    # SQL 表 meta 读取检查
    guardrails.append(SQLEntityCheck())

    # JOIN 路径合理性检测
    guardrails.append(BridgeTableCheck())

    # SQL 语义消歧检查
    guardrails.append(SQLDisambigCheck())

    return guardrails
