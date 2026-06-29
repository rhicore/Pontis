"""Sub-agent tool executor — 创建子智能体执行任务。"""
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


class AgentExecutor:
    """可调用对象：按父工具权限创建子智能体。

    只读父 agent → readonly 子 agent（无 agent 工具）
    有写工具的父 agent → writer 子 agent（无 agent 工具）
    """

    def __init__(self, parent_registry: "ToolRegistry", writable: bool = False):
        self._parent_registry = parent_registry
        self._writable = writable

    def __call__(self, workspace, arguments: dict, **kwargs) -> str:
        from agent.agent import PontusAgent
        from agent.config import AgentSpec
        from agent.guardrail import build_guardrails
        from agent.tools import ToolRegistry
        from agent.prompt import build_prompt_messages

        task = arguments.get("task", "")
        if not task:
            return "错误: task 参数不能为空"

        max_rounds = arguments.get("max_rounds", 40)
        desc = arguments.get("description", "")

        if desc:
            print(f"  \033[90m[子智能体: {desc}]\033[0m")

        # 构建子智能体工具集：复制父工具 - agent（防递归）
        sub_tools = ToolRegistry()
        for name in self._parent_registry.tool_names:
            if name != "agent":
                schema, executor = self._parent_registry._tools[name]
                sub_tools.register(name, schema, executor)

        # 按父 agent 权限显式选择子 agent 工具集。
        sub_tool_names = [name for name in self._parent_registry.tool_names if name != "agent"]
        if self._writable:
            sub_prompts = [
                "base", "tool", "ontology", "sql",
                "guardrail", "readme",
            ]
            sub_label = "writer"
        else:
            sub_prompts = [
                "base", "tool", "ontology", "sql",
                "guardrail", "project", "readme",
            ]
            sub_label = "readonly"
        sub_spec = AgentSpec(
            project_path=workspace.project_path,
            tools=sub_tool_names,
            prompts=sub_prompts,
            max_rounds=max_rounds,
        )
        sub_spec.guardrails = build_guardrails(
            sub_spec,
            ["round_limit", "exploration_check", "sql_check", "bridge_check", "disambig_check"],
        )
        sub_prompt = build_prompt_messages(sub_spec)

        sub_agent = PontusAgent(
            workspace.project_path,
            tools=sub_tools,
            system_prompt=sub_prompt,
            guardrails=sub_spec.guardrails,
        )
        sub_agent.max_rounds = max_rounds

        # 执行任务
        print(f"  \033[90m    子智能体启动 ({sub_label}, max {max_rounds} rounds)\033[0m")
        result = sub_agent.chat(task)
        print(f"  \033[90m    子智能体完成\033[0m")

        # 收集工具调用统计
        tool_calls = []
        system_count = len(getattr(sub_agent, "_system_messages", []))
        for msg in sub_agent.messages[system_count:]:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                tcs = msg.get("tool_calls") or []
                for tc in tcs:
                    tool_calls.append(tc["function"]["name"])

        # 构建结构化报告
        report = {
            "status": "completed" if len(tool_calls) < max_rounds else "max_rounds_reached",
            "rounds_used": len(tool_calls),
            "max_rounds": max_rounds,
            "tools_called": list(dict.fromkeys(tool_calls)),  # 去重保序
            "result": result or "(无文本输出)",
        }

        return json.dumps(report, ensure_ascii=False, indent=2)
