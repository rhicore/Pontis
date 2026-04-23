"""Query tool prompt — SQL 执行工具。"""

DESCRIPTION = "在数据库上执行只读 SQL 查询，返回结果或错误信息。"

DETAIL = """\
参数：
- sql (必填): SQL 查询语句，只允许 SELECT
- file (必填): 数据库文件路径，如 "my_data.sqlite"、"data.db"
- limit: 返回最大行数，默认 100

行为：
- 只执行 SELECT 语句，其他语句（INSERT/UPDATE/DELETE/CREATE/DROP/ALTER）会被拒绝
- 执行成功时返回结果行（表格格式），最多返回 limit 行
- 执行失败时返回错误信息，你可以根据错误修正 SQL 后重试
- 大结果集会被截断，末尾提示总行数

注意：
- 列名有空格或特殊字符时用双引号包裹，如 SELECT "Player Name" FROM ...
- 这是一个只读工具，不会修改任何数据\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
