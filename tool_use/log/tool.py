"""Log tool — 调试模式下 agent 反馈记录。"""
import logging

_logger = logging.getLogger("agent.debug")


def log_command(category: str, feedback: str) -> str:
    """记录调试反馈到日志。

    Args:
        category: 问题类别（tool_result / tool_missing / tool_ux / prompt_unclear）
        feedback: 具体问题描述
    """
    valid = {"tool_result", "tool_missing", "tool_ux", "prompt_unclear"}
    if category not in valid:
        return f"无效类别: {category}，可选: {', '.join(sorted(valid))}"

    _logger.info(f"[DEBUG:{category}] {feedback}")
    return "已记录"
