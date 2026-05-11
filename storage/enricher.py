"""Enricher — 现场计算的虚属性（基于 labels 匹配）。

不存储在 _meta.yml 中，而是在 store.get_meta() 时按需计算。
只补充 meta 中缺失的字段，已有则跳过（尊重 extractor 预计算的值）。

通过 provider 实例的 prop_registry / dir_props / common_file_props 获取属性注册表，
不硬编码导入任何具体存储类型的模块。

使用：
    from storage.enricher import enrich_meta
    meta = enrich_meta(meta, project_path, file_rel_path, entity_path, store=store)
"""
import os
from typing import Callable, Dict, List, Optional, Set


def enrich_meta(meta: dict, project_path: str, file_rel_path: str,
                entity_path: str = "",
                include_props: Optional[List[str]] = None,
                store=None, _visiting: Optional[Set[str]] = None) -> dict:
    """补充虚属性到 meta dict 中。

    Args:
        meta: 已有的 meta 字典
        project_path: 项目根目录绝对路径
        file_rel_path: 文件相对路径
        entity_path: 实体名称
        include_props: 显式指定需要的虚属性。
            None = 全部，[] = 无（由 Store 层控制），["file_size", ...] = 指定
        store: provider 实例，提供 prop_registry / dir_props / common_file_props
        _visiting: 运行时环路检测的访问状态集
    """
    result = dict(meta)
    full_path = os.path.join(project_path, file_rel_path)

    prop_registry = store.prop_registry if store else {}
    dir_props = store.dir_props if store else {}
    common_fallback = store.common_file_props if store else {}

    # 目录虚属性
    if os.path.isdir(full_path) and not entity_path:
        _apply_group(result, dir_props, project_path, file_rel_path, entity_path,
                     include_props)
        return result

    # 按 labels 查注册表（扁平标签：逐个查 key）
    labels = result.get("labels", [])
    matched = False

    if labels:
        for label in labels:
            if label in prop_registry:
                _apply_group(result, prop_registry[label], project_path,
                             file_rel_path, entity_path, include_props)
                matched = True
                break

    if not matched and os.path.isfile(full_path):
        _apply_group(result, common_fallback, project_path, file_rel_path,
                     entity_path, include_props)

    return result


def _apply_group(result: dict, props: Dict[str, Callable],
                 project_path: str, file_rel_path: str, entity_path: str,
                 include_props: Optional[List[str]] = None):
    """应用一组虚属性，只补充缺失的字段。"""
    for key, func in props.items():
        if include_props is not None and key not in include_props:
            continue
        if key not in result:
            try:
                value = func(project_path, file_rel_path, entity_path)
                if value is not None:
                    result[key] = value
            except Exception:
                pass
