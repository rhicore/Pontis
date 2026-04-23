"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看文件或逻辑实体的元数据。"

DETAIL = """\
参数：
- path (必填): ref 字符串（文件路径、path::entity、或 ent_id）
- property: 指定要查看的属性，支持字符串或列表
  - 字符串: property="sample"
  - 列表: property=["cardinality", "sample", "topk"]
  - 不指定 property 时返回默认概况（brief + detail + 核心统计）
- all: 设为 true 显示全部字段
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
