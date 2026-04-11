"""Virtual Properties — 现场计算的虚属性

不存储在 _meta.yml 中，而是在 store.get_meta() 时按需计算。
只补充 meta 中缺失的字段，已有则跳过（尊重 extractor 预计算的值）。

使用：
    from storage.virtual_props import enrich_meta
    meta = enrich_meta(meta, project_path, file_rel_path, entity_path)

虚属性提取器按类型分模块存放在 virtual_props_extract/ 包中。
新增类型只需在该包下新建模块并在 __init__.py 中注册。
"""
import os
from datetime import datetime
from typing import Callable, Dict

from storage.virtual_props_extract import PROP_REGISTRY, DIR_PROPS, COMMON_FILE_PROPS


def enrich_meta(meta: dict, project_path: str, file_rel_path: str,
                entity_path: str = "") -> dict:
    """补充虚属性到 meta dict 中。"""
    result = dict(meta)
    full_path = os.path.join(project_path, file_rel_path)

    # 目录虚属性
    if os.path.isdir(full_path) and not entity_path:
        _apply_group(result, DIR_PROPS, project_path, file_rel_path, entity_path)
        return result

    # 确定类型后缀
    if entity_path:
        name = entity_path.split("/")[-1] if "/" in entity_path else entity_path
        suffix = ("." + name.split(".")[-1].lower()) if "." in name else ""
    else:
        suffix = ""

    # 按后缀匹配注册表
    if suffix in PROP_REGISTRY:
        _apply_group(result, PROP_REGISTRY[suffix], project_path, file_rel_path, entity_path)
        return result

    # 兜底：文件大小 + modified_at
    if os.path.isfile(full_path):
        _apply_group(result, COMMON_FILE_PROPS, project_path, file_rel_path, entity_path)

    return result


def _apply_group(result: dict, props: Dict[str, Callable],
                 project_path: str, file_rel_path: str, entity_path: str):
    """应用一组虚属性，只补充缺失的字段。"""
    for key, func in props.items():
        if key not in result:
            try:
                value = func(project_path, file_rel_path, entity_path)
                if value is not None:
                    result[key] = value
            except Exception:
                pass
