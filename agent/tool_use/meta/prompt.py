"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看文件或逻辑实体的元数据。"

DETAIL = """\
参数：
- ref (必填): 实体名称
- property: 指定要查看的属性，支持字符串或列表
  - 字符串: property="sample"
  - 列表: property=["cardinality", "sample", "topk"]
  - 不指定 property 时返回默认概况（brief + detail + 核心统计）
- all: 设为 true 显示全部字段；只在调试工具输出时使用，正常探索优先用 property 精确读取，避免读取内部字段和无关统计
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
