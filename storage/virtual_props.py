"""Virtual Properties — 现场计算的虚属性

不存储在 _meta.yml 中，而是在 store.get_meta() 时按需计算。
只补充 meta 中缺失的字段，已有则跳过（尊重 extractor 预计算的值）。

使用：
    from storage.virtual_props import enrich_meta
    meta = enrich_meta(meta, project_path, file_rel_path, entity_path)
"""
import os
import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, Optional


def enrich_meta(meta: dict, project_path: str, file_rel_path: str,
                entity_path: str = "") -> dict:
    """补充虚属性到 meta dict 中。

    Args:
        meta: 原始 meta dict（不会被修改）
        project_path: 项目根路径
        file_rel_path: 相对文件路径
        entity_path: 实体路径（如 "users.table"）

    Returns:
        补充了虚属性的新 dict
    """
    result = dict(meta)

    full_path = os.path.join(project_path, file_rel_path)

    # 目录虚属性
    if os.path.isdir(full_path) and not entity_path:
        _apply_group(result, _DIR_PROPS, project_path, file_rel_path, entity_path)
        return result

    # 确定类型后缀
    if entity_path:
        name = entity_path.split("/")[-1] if "/" in entity_path else entity_path
        if "." in name:
            suffix = "." + name.split(".")[-1].lower()
        else:
            suffix = ""
    else:
        suffix = ""

    # 按后缀匹配
    if suffix in _PROP_REGISTRY:
        _apply_group(result, _PROP_REGISTRY[suffix], project_path, file_rel_path, entity_path)
        return result

    # 兜底：文件大小 + modified_at
    if os.path.isfile(full_path):
        if "file_size" not in result:
            try:
                result["file_size"] = os.path.getsize(full_path)
            except OSError:
                pass
        if "modified_at" not in result:
            try:
                mtime = os.path.getmtime(full_path)
                result["modified_at"] = datetime.fromtimestamp(mtime).isoformat()
            except OSError:
                pass

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


# ========== 目录虚属性 ==========

def _vp_child_count(project_path: str, file_rel_path: str, entity_path: str) -> int:
    full = os.path.join(project_path, file_rel_path)
    entries = [e for e in os.listdir(full) if not e.startswith('.')]
    return len(entries)


def _vp_file_count(project_path: str, file_rel_path: str, entity_path: str) -> int:
    full = os.path.join(project_path, file_rel_path)
    return sum(1 for e in os.listdir(full)
               if not e.startswith('.') and os.path.isfile(os.path.join(full, e)))


def _vp_subdir_count(project_path: str, file_rel_path: str, entity_path: str) -> int:
    full = os.path.join(project_path, file_rel_path)
    return sum(1 for e in os.listdir(full)
               if not e.startswith('.') and os.path.isdir(os.path.join(full, e)))


_DIR_PROPS = {
    "child_count": _vp_child_count,
    "file_count": _vp_file_count,
    "subdir_count": _vp_subdir_count,
}


# ========== 文件通用虚属性 ==========

def _vp_file_size(project_path: str, file_rel_path: str, entity_path: str) -> int:
    full = os.path.join(project_path, file_rel_path)
    return os.path.getsize(full)


def _vp_modified_at(project_path: str, file_rel_path: str, entity_path: str) -> str:
    full = os.path.join(project_path, file_rel_path)
    mtime = os.path.getmtime(full)
    return datetime.fromtimestamp(mtime).isoformat()


# ========== DB 文件虚属性 ==========

def _vp_db_table_count(project_path: str, file_rel_path: str,
                       entity_path: str) -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


def _vp_db_view_count(project_path: str, file_rel_path: str,
                      entity_path: str) -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


def _vp_db_index_count(project_path: str, file_rel_path: str,
                       entity_path: str) -> Optional[int]:
    db_path = os.path.join(project_path, file_rel_path)
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


# ========== DB 表虚属性 ==========

def _vp_db_table_row_count(project_path: str, file_rel_path: str,
                            entity_path: str) -> Optional[int]:
    table_name = entity_path.replace(".table", "").replace(".view", "").split("/")[-1]
    db_path = os.path.join(project_path, file_rel_path)

    if not os.path.isfile(db_path):
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None


def _vp_db_table_column_count(project_path: str, file_rel_path: str,
                               entity_path: str) -> Optional[int]:
    table_name = entity_path.replace(".table", "").replace(".view", "").split("/")[-1]
    db_path = os.path.join(project_path, file_rel_path)

    if not os.path.isfile(db_path):
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        count = len(cursor.fetchall())
        conn.close()
        return count
    except Exception:
        return None


# ========== 注册表 ==========

_PROP_REGISTRY = {
    ".db": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
        "table_count": _vp_db_table_count,
        "view_count": _vp_db_view_count,
        "index_count": _vp_db_index_count,
    },
    ".csv": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".tsv": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".json": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".yaml": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".yml": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".xml": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".toml": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".md": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".txt": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".sql": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".log": {
        "file_size": _vp_file_size,
        "modified_at": _vp_modified_at,
    },
    ".table": {
        "row_count": _vp_db_table_row_count,
        "column_count": _vp_db_table_column_count,
    },
    ".view": {
        "row_count": _vp_db_table_row_count,
        "column_count": _vp_db_table_column_count,
    },
}
