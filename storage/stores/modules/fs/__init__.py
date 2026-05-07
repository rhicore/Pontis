"""storage.stores.modules.fs — 文件系统存储的虚属性模块。

PROP_REGISTRY: label → {prop_name: callable}
扁平标签匹配：遍历 _labels，逐个查注册表。
"""
from typing import Callable, Dict

from .file import COMMON_FILE_PROPS, FILE_PROPS
from .sqlite import DB_PROPS, TABLE_PROPS, VIEW_PROPS
from .directory import DIR_PROPS

# label → 属性组（扁平标签 key）
PROP_REGISTRY: Dict[str, Dict[str, Callable]] = {
    # 文件子类型（优先匹配具体类型）
    "db": DB_PROPS,
    "csv": FILE_PROPS,
    "json": FILE_PROPS,
    "text": FILE_PROPS,
    "yaml": FILE_PROPS,
    "xml": FILE_PROPS,
    "toml": FILE_PROPS,
    "hcl": FILE_PROPS,
    # 通用文件 fallback
    "file": FILE_PROPS,
    # 表/视图
    "table": TABLE_PROPS,
    "view": VIEW_PROPS,
}

__all__ = ["PROP_REGISTRY", "DIR_PROPS", "COMMON_FILE_PROPS"]
