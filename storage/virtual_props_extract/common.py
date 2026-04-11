"""通用文件虚属性 — file_size, modified_at"""
import os
from datetime import datetime

from typing import Optional


def file_size(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[int]:
    full = os.path.join(project_path, file_rel_path)
    try:
        return os.path.getsize(full)
    except OSError:
        return None


def modified_at(project_path: str, file_rel_path: str, entity_path: str = "") -> Optional[str]:
    full = os.path.join(project_path, file_rel_path)
    try:
        mtime = os.path.getmtime(full)
        return datetime.fromtimestamp(mtime).isoformat()
    except OSError:
        return None


# 可被其他模块复用的通用 prop 组
COMMON_FILE_PROPS = {
    "file_size": file_size,
    "modified_at": modified_at,
}
