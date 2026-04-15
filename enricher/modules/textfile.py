"""文本/数据文件虚属性 — file_size, modified_at（通用文件类型）"""
from typing import Dict, Callable

from .common import COMMON_FILE_PROPS


# 需要通用文件属性的类型列表
_TEXT_TYPES = (".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".toml",
              ".md", ".txt", ".sql", ".log")

PROPS: Dict[str, Dict[str, Callable]] = {
    suffix: dict(COMMON_FILE_PROPS) for suffix in _TEXT_TYPES
}
