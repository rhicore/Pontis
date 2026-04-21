"""Sub-agent tool executor — 创建子智能体执行任务。"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.tools import ToolRegistry


class AgentExecutor:
    """可调用对象：按父模式创建子智能体。

    readonly 父 → readonly 子（无 agent 工具）
    writer 父  → writer 子（无 agent 工具）
    """

    def __init__(self, parent_registry: "ToolRegistry", mode: str = "writer"):
        self._parent_registry = parent_registry
        self._mode = mode

    def __call__(self, store, arguments: dict) -> str:
        from agent.agent import PontisAgent
        from agent.tools import ToolRegistry
        from agent.prompt import build_prompt

        task = arguments.get("task", "")
        if not task:
            return "错误: task 参数不能为空"

        max_rounds = arguments.get("max_rounds", 15)
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
        if self._mode == "readonly":
            sub_prompt = build_prompt("readonly", store.project_path)
        else:
            sub_prompt = build_prompt("sub_agent", store.project_path)

        sub_agent = PontisAgent(
            store.project_path,
            tools=sub_tools,
            system_prompt=sub_prompt,
        )

        # 执行任务
        print(f"  \033[90m    子智能体启动 ({self._mode}, max_rounds={max_rounds})\033[0m")
        result = sub_agent.chat(task, max_rounds=max_rounds)
        print(f"  \033[90m    子智能体完成\033[0m")

        return result or "(子智能体未返回结果)"
