"""Bash tool prompt — Shell 命令执行。"""

DESCRIPTION = "执行 shell 命令。仅在其他工具无法完成任务时作为最后手段使用。"

DETAIL = """\
参数：
- command (必填): 要执行的 shell 命令
- timeout: 超时秒数，默认 120

注意：
- 工作目录已设为项目路径，命令中的路径相对于项目根目录
- 只进行只读操作（如 ls, cat, head, wc, sqlite3 查询），不要修改数据
- 不要修改 .pontis/ 目录下的任何内容
- 如果 glob/meta/read 能完成任务，不要用 bash\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
