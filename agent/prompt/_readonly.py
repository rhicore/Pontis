"""只读模式层 — 仅 readonly agent 使用的追加提示词。

身份定义 + 行为规范。SQL 生成规范已移至 _sql.py（所有模式共享）。
"""

_READONLY_ADDITIONS = r"""

!你当前处于只读模式，不要使用bash等工具执行修改任何文件的写入操作。

"""


def get_readonly_additions() -> str:
    return _READONLY_ADDITIONS
