"""Meta tool prompt — 元数据查看。"""

DESCRIPTION = "查看文件或逻辑实体的元数据。"

DETAIL = """\
参数：
- path (必填): ref 字符串（文件路径、path::entity、或 ent_id）
- property: 指定要查看的属性，支持字符串或列表
  - 字符串: property="sample"
  - 列表: property=["cardinality", "sample", "topk"]
  - 不指定 property 时返回默认概况（brief + detail + 核心统计）
- all: 设为 true 显示全部字段。⚠️ 会返回大量无关字段（file_size, created_at, modified_at 等），仅在确实需要时使用

高效用法：
- 优先用 property=["sample", "topk", "cardinality"] 精准读取需要的字段
- ⚠️ 避免使用 all=true，除非确实需要 file_size、created_at 等字段。all=true 会返回大量无关字段浪费上下文
- 如果上下文中已提供列的统计信息，不要重复调 meta 获取

典型用法：
- meta(path="event.db::event.status.TEXT.col", property=["sample", "topk"])
- meta(path="event.db::event.table") → 默认返回概况
- meta(path="event.db::event.table", property="detail") → 只看 AI 总结\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
