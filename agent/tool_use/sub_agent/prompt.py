"""Sub-agent tool prompt — 启动子智能体执行复杂任务。"""

DESCRIPTION = "启动一个子智能体来执行复杂的多步骤任务。子智能体拥有与你相同的工具能力，但无法再启动子智能体。"

DETAIL = """\
子智能体是一个独立的智能体会话，拥有完整的工具集（只读+写入）。

参数：
- task (必填): 详细的任务描述。子智能体看不到你的对话历史，所以需要提供完整的背景信息
- max_rounds: 子智能体最大 tool call 轮次，默认 40
- description: 简短的任务摘要，用于日志显示

返回：
- 结构化 JSON 报告：status（completed / max_rounds_reached）、rounds_used、tools_called、result
- status 为 max_rounds_reached 时，result 包含子智能体的任务报告\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
