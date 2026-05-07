"""Write 工具共用 — 实体引用解析。

所有 ref 都通过 glob 匹配，必须唯一命中。实体 ID (ent_xxx) 为内部属性，不对外暴露。
"""


def resolve_entity(obj, ref: str) -> tuple:
    """将实体引用解析为唯一 ent_id。

    匹配逻辑：
      - 精确名称（无通配符、无 URN 语法）→ 直接 store 查找
      - glob/URN 模式 → glob_command 匹配，必须唯一

    Returns:
        (ent_id, error_msg)
        成功时 error_msg 为 None，失败时 ent_id 为 None
    """
    store = obj if not hasattr(obj, 'get_store') else obj.get_store()

    # 精确名称快速路径（无通配符）
    has_wildcards = any(c in ref for c in '*?[]')

    if not has_wildcards:
        ent_id = store._name_to_id(ref)
        if ent_id:
            return (ent_id, None)

    # glob/URN 模式匹配（含 / 路径名、: 标签过滤、* 通配符）
    from tool.glob.tool import glob_command
    output = glob_command(obj, ref)

    if output.startswith("No objects"):
        return (None, f"未找到匹配的实体: {ref}")

    # 解析结果行，提取 name 并查找 ent_id
    lines = [l for l in output.strip().split("\n") if l.strip() and not l.startswith("(")]
    matched = []
    for line in lines:
        parts = line.split("\t")
        if parts:
            eid = store._name_to_id(parts[0])
            if eid:
                matched.append((eid, parts[0]))

    if not matched:
        return (None, f"未找到匹配的实体: {ref}")
    if len(matched) > 1:
        names = [n for _, n in matched]
        return (None, f"匹配到 {len(matched)} 个实体，请使用更精确的模式:\n  " + "\n  ".join(names))

    return (matched[0][0], None)
