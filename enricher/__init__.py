"""Enricher — 现场计算的虚属性

不存储在 _meta.yml 中，而是在 store.get_meta() 时按需计算。
只补充 meta 中缺失的字段，已有则跳过（尊重 extractor 预计算的值）。

使用：
    from enricher import enrich_meta
    meta = enrich_meta(meta, project_path, file_rel_path, entity_path)

虚属性模块按类型分模块存放在 modules/ 包中。
新增类型只需在该包下新建模块并在 __init__.py 中注册。
"""
import os
from typing import Callable, Dict, List, Optional, Set

from enricher.modules import PROP_REGISTRY, DIR_PROPS, COMMON_FILE_PROPS


def enrich_meta(meta: dict, project_path: str, file_rel_path: str,
                entity_path: str = "",
                include_props: Optional[List[str]] = None,
                store=None, _visiting: Optional[Set[str]] = None) -> dict:
    """补充虚属性到 meta dict 中。

    Args:
        meta: 已有的 meta 字典
        project_path: 项目根目录绝对路径
        file_rel_path: 文件相对路径
        entity_path: 实体名称（如 "users.table"）
        include_props: 显式指定需要的虚属性。
            None = 全部，[] = 无（由 Store 层控制），["file_size", ...] = 指定
        store: Store 实例，供需要图谱查询的虚属性函数使用
        _visiting: 运行时环路检测的访问状态集，虚属性函数内部调用
            store.get_meta() 时必须透传此集合以防止无限递归
    """
    result = dict(meta)
    full_path = os.path.join(project_path, file_rel_path)

    # 目录虚属性
    if os.path.isdir(full_path) and not entity_path:
        _apply_group(result, DIR_PROPS, project_path, file_rel_path, entity_path,
                     include_props)
        return result

    # 确定类型后缀
    if entity_path:
        name = entity_path.split("/")[-1] if "/" in entity_path else entity_path
        suffix = ("." + name.split(".")[-1].lower()) if "." in name else ""
    else:
        suffix = ""

    # 按后缀匹配注册表
    if suffix in PROP_REGISTRY:
        _apply_group(result, PROP_REGISTRY[suffix], project_path, file_rel_path, entity_path,
                     include_props)
        return result

    # 兜底：文件大小 + modified_at
    if os.path.isfile(full_path):
        _apply_group(result, COMMON_FILE_PROPS, project_path, file_rel_path, entity_path,
                     include_props)

    return result


def _apply_group(result: dict, props: Dict[str, Callable],
                 project_path: str, file_rel_path: str, entity_path: str,
                 include_props: Optional[List[str]] = None):
    """应用一组虚属性，只补充缺失的字段。

    当 include_props 非空时，只计算列表中指定的属性；
    当 include_props 为 None 时，计算所有已注册的虚属性。
    """
    for key, func in props.items():
        # 如果调用方显式指定了需要的属性列表，跳过不在列表中的属性
        if include_props is not None and key not in include_props:
            continue
        if key not in result:
            try:
                value = func(project_path, file_rel_path, entity_path)
                if value is not None:
                    result[key] = value
            except Exception:
                pass
