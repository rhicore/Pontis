"""只读模式层 — 仅 readonly agent 使用的追加提示词。

身份定义 + 行为规范。SQL 生成规范已移至 _sql.py（所有模式共享）。
"""

_READONLY_ADDITIONS = r"""

你当前处于只读模式。

- 只允许读取、分析、查询和解释已有信息
- 不要创建、更新或删除任何图谱实体
- 不要使用 bash 或其他工具执行写文件、改文件、删文件操作

"""


def get_readonly_additions() -> str:
    return _READONLY_ADDITIONS
