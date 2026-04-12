"""update_meta — 更新文件或实体的元数据"""
from typing import Dict, Optional


def update_meta_command(store, path: str, fields: Dict,
                        entity_path: Optional[str] = None) -> str:
    """更新文件或实体的 meta，合并写入指定字段。

    Args:
        store: ProjectStore 实例
        path: 文件路径
        fields: 要更新的字段键值对，如 {"brief": "...", "detail": "..."}
        entity_path: 实体路径（可选，不提供则为文件级 meta）
    """
    # 检查 meta 是否存在
    target_desc = f"{path}::{entity_path}" if entity_path else path

    if not store.meta_exists(path, entity_path or ""):
        return f"错误: 未找到 {target_desc} 的 meta，请确认路径正确"

    # 合并写入
    store.write_meta(path, fields, entity_path or "")

    # 读取更新后的完整 meta 用于返回
    updated = store.get_meta(path, entity_path or "")

    result_parts = [f"已更新 {target_desc} 的元数据:"]
    for key, value in fields.items():
        val_str = str(value)
        if len(val_str) > 200:
            val_str = val_str[:200] + "..."
        result_parts.append(f"  {key}: {val_str}")

    return "\n".join(result_parts)
