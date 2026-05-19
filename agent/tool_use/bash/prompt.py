"""Bash tool prompt — Shell 命令执行。"""

DESCRIPTION = "执行只读 shell 命令。"

DETAIL = """\
参数：
- command (必填): 要执行的 shell 命令
- timeout: 超时秒数，默认 120

硬约束：
- 工作目录已设为项目路径，命令中的路径相对于项目根目录
- 只有 local fs workspace 才允许执行；非本地 source 必须使用 storage-backed 工具
- 命令必须是只读操作；输出写入、文件修改、联网、安装依赖、删除数据不属于该工具范围
- 路径必须位于当前 workspace 内
- public benchmark 的 gold/output 目录不是解题数据源\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
