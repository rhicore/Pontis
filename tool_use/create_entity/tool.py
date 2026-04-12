"""create_entity — 创建逻辑实体"""
import json
from typing import Dict, List, Optional


def create_entity_command(store, path: str, entity_type: str, entity_name: str,
                          meta: Optional[Dict] = None,
                          edges: Optional[List[Dict]] = None) -> str:
    """创建一个新的逻辑实体，可同时写入 meta 和添加关系边。

    Args:
        store: ProjectStore 实例
        path: 文件路径，如 'event.db'
        entity_type: 实体类型后缀，如 'view', 'rel', 'pattern'
        entity_name: 实体名称，如 'user_event_join.view'
        meta: 初始 meta 数据（可选）
        edges: 要添加的关系边列表（可选），格式: [{"from": "...", "type": "...", "to": "..."}]
    """
    # 构造 entity_path：如果名称已含后缀则不重复加
    if entity_name.endswith(f".{entity_type}"):
        entity_path = entity_name
    else:
        entity_path = f"{entity_name}.{entity_type}"

    # 创建实体目录
    entity_dir = store.create_entity_dir(path, entity_path)

    # 写入 meta
    if meta:
        store.write_meta(path, meta, entity_path)

    # 添加关系边
    added_edges = 0
    if edges:
        before_count = len(_read_edges(store))
        store.add_edges(edges)
        after_count = len(_read_edges(store))
        added_edges = after_count - before_count

    # 构建返回信息
    result_parts = [f"已创建实体: {path}::{entity_path}"]
    if meta:
        result_parts.append(f"Meta 字段: {', '.join(meta.keys())}")
    if edges:
        result_parts.append(f"添加了 {added_edges} 条关系边")
        for e in edges:
            result_parts.append(f"  {e['from']} --[{e['type']}]--> {e['to']}")

    return "\n".join(result_parts)


def _read_edges(store) -> list:
    """读取现有边列表。"""
    import os
    import yaml
    edges_path = os.path.join(store._pontis_root, "_edges.yml")
    if not os.path.exists(edges_path):
        return []
    with open(edges_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get("edges", [])
