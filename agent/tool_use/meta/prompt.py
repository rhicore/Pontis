"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看文件或逻辑实体的元数据。"

DETAIL = """\
参数：
- ref (必填): 实体名称
- property: 指定要查看的属性，支持字符串或列表
  - 字符串: property="sample"
  - 列表: property=["cardinality", "sample", "topk"]
  - 不指定 property 时返回默认概况（brief + detail + 核心统计）
- all: 设为 true 显示全部字段

返回：
- 文件实体返回文件级 brief/detail、大小、行数或结构统计
- 表/列实体返回 schema、统计、样例、topk、null_count 等可用字段
- 派生实体返回 brief/detail 及相邻实体摘要
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
