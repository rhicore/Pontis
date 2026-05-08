"""Sub-agent tool executor — 创建子智能体执行任务。"""
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


class AgentExecutor:
    """可调用对象：按父模式创建子智能体。

    readonly 父 → readonly 子（无 agent 工具）
    writer 父  → writer 子（无 agent 工具）
    """

    def __init__(self, parent_registry: "ToolRegistry", mode: str = "writer"):
        self._parent_registry = parent_registry
        self._mode = mode

    def __call__(self, store, arguments: dict, **kwargs) -> str:
        from agent.agent import PontusAgent
        from agent.config import AgentSpec
        from agent.tools import ToolRegistry
        from agent.prompt import build_prompt

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

        # 按模式选择 prompt
        sub_mode = "readonly" if self._mode == "readonly" else "sub_agent"
        sub_prompt = build_prompt(AgentSpec(mode=sub_mode, project_path=workspace.project_path))

        sub_agent = PontusAgent(
            workspace.project_path,
            tools=sub_tools,
            system_prompt=sub_prompt,
        )
        sub_agent.max_rounds = max_rounds

        # 执行任务
        print(f"  \033[90m    子智能体启动 ({sub_mode}, max {max_rounds} rounds)\033[0m")
        result = sub_agent.chat(task)
        print(f"  \033[90m    子智能体完成\033[0m")

        # 收集工具调用统计
        tool_calls = []
        for msg in sub_agent.messages[1:]:  # skip system prompt
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
