"""目录虚属性 — child_count, file_count, subdir_count"""
import os
from typing import Dict, Callable


def child_count(project_path: str, file_rel_path: str, entity_path: str = "") -> int:
    full = os.path.join(project_path, file_rel_path)
    return len([e for e in os.listdir(full) if not e.startswith('.')])


def file_count(project_path: str, file_rel_path: str, entity_path: str = "") -> int:
    full = os.path.join(project_path, file_rel_path)
    return sum(1 for e in os.listdir(full)
               if not e.startswith('.') and os.path.isfile(os.path.join(full, e)))


def subdir_count(project_path: str, file_rel_path: str, entity_path: str = "") -> int:
    full = os.path.join(project_path, file_rel_path)
    return sum(1 for e in os.listdir(full)
               if not e.startswith('.') and os.path.isdir(os.path.join(full, e)))


DIR_PROPS: Dict[str, Callable] = {
    "child_count": child_count,
    "file_count": file_count,
    "subdir_count": subdir_count,
}
