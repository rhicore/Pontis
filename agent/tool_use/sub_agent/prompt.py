"""Sub-agent tool prompt — 启动子智能体执行复杂任务。"""

DESCRIPTION = "启动一个子智能体来执行复杂的多步骤任务。子智能体拥有与你相同的工具能力，但无法再启动子智能体。"

DETAIL = """\
子智能体是一个独立的智能体会话，拥有完整的工具集（只读+写入），从零开始执行你指定的任务。

适用场景：
- 需要多步骤探索和分析的复杂任务
- 需要生成或更新大量实体元数据的批量工作
- 你希望在不占用当前上下文的情况下独立完成的工作

参数：
- task (必填): 详细的任务描述。子智能体看不到你的对话历史，所以需要提供完整的背景信息
- max_rounds: 子智能体最大 tool call 轮次，默认 40
- description: 简短的任务摘要，用于日志显示

使用建议：
- task 中要包含完整的上下文：目标、已知信息、具体要求
- 不要写 "根据你的发现来修复问题" 这类模糊指令——把你的理解写进 task
- 需要简短回复时明确说明（如 "200字以内回复"）
- 子智能体返回结构化 JSON 报告：status（completed / max_rounds_reached）、rounds_used、tools_called、result
- 如果 status 为 max_rounds_reached，result 中会包含子智能体的任务报告（已完成/未完成列表），据此补做剩余部分
- 列数较多的表（>10列）建议设置更高的 max_rounds\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
