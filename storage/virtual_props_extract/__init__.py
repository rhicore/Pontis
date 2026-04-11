"""virtual_props_extract — 虚属性提取器包

每个模块按文件/实体类型组织虚属性计算函数。
新类型只需新建模块并在这里注册即可。
"""
from typing import Dict, Callable

from .directory import DIR_PROPS
from .database import PROPS as _db_props
from .table import PROPS as _table_props
from .textfile import PROPS as _textfile_props
from .common import COMMON_FILE_PROPS

# 合并所有类型的注册表
PROP_REGISTRY: Dict[str, Dict[str, Callable]] = {}
for _props in (_db_props, _table_props, _textfile_props):
    PROP_REGISTRY.update(_props)

__all__ = ["PROP_REGISTRY", "DIR_PROPS", "COMMON_FILE_PROPS"]
