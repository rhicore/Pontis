"""storage.stores.modules.fs — 文件系统存储的虚属性模块。

PROP_REGISTRY: label → {prop_name: callable}
匹配时用 label 首段或完整路径查找。
"""
from typing import Callable, Dict

from .file import COMMON_FILE_PROPS, FILE_PROPS
from .sqlite import DB_PROPS, TABLE_PROPS, VIEW_PROPS
from .directory import DIR_PROPS

# label → 属性组
# 完整 label（如 "file/db"）优先，首段（如 "file"）次之
PROP_REGISTRY: Dict[str, Dict[str, Callable]] = {
    # 文件类型
    "file/db": DB_PROPS,
    "file/csv": FILE_PROPS,
    "file/json": FILE_PROPS,
    "file/text": FILE_PROPS,
    "file/yaml": FILE_PROPS,
    "file/xml": FILE_PROPS,
    "file/toml": FILE_PROPS,
    "file/hcl": FILE_PROPS,
    "file": FILE_PROPS,
    # 表/视图
    "table": TABLE_PROPS,
    "view": VIEW_PROPS,
}

__all__ = ["PROP_REGISTRY", "DIR_PROPS", "COMMON_FILE_PROPS"]
