"""Search tool prompt — 语义检索。"""

DESCRIPTION = "语义检索工具，当不确定具体路径或名称，仅有模糊意图时使用。"

DETAIL = """\
参数：
- path_pattern (必填): glob 模式限定搜索范围
- query (必填): 自然语言描述
- offset: 起始位置（从 0 开始），默认 0
- limit: 每页最大条数，默认 100，最大 500

结果截断时会提示总数和当前范围，使用 offset 参数翻页。

注意：
- 如果知道确切的名称或路径模式，优先使用 glob（更快更精确）
- search 是 glob 的模糊补充，不是替代\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
