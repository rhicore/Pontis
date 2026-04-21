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
- 写 brief/detail 时，优先用 property=["sample", "topk", "cardinality"] 精准读取
- 避免用 all=true，大部分场景下 property 就够了
- 如果 task 描述中已提供列的统计信息，不要再调 meta 重复获取

典型用法：
- meta(path="event.db::event.status.TEXT.col", property=["sample", "topk"])
- meta(path="event.db::event.table") → 默认返回概况
- meta(path="event.db::event.table", property="detail") → 只看 AI 总结\
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
