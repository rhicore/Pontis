"""Utility functions"""
import hashlib
import os
import re
from datetime import datetime
from typing import Optional


def safe_filename(name: str) -> str:
    """Convert a string to a safe filename"""
    # Remove or replace unsafe characters
    safe = re.sub(r'[<>:"/\\|?*]', '_', name)
    safe = safe.strip('. ')
    if not safe:
        safe = '_'
    return safe


def hash_file(filepath: str, algorithm: str = "md5") -> str:
    """Calculate hash of a file"""
    hasher = hashlib.new(algorithm)
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_string(s: str, algorithm: str = "md5") -> str:
    """Calculate hash of a string"""
    return hashlib.new(algorithm, s.encode()).hexdigest()


def get_file_mtime(filepath: str) -> Optional[datetime]:
    """Get file modification time as datetime"""
    try:
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime)
    except (OSError, FileNotFoundError):
        return None


def truncate_string(s: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate a string to max_length"""
    if len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix


def format_bytes(size: int) -> str:
    """Format bytes to human readable string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def infer_data_type(value: any) -> str:
    """Infer data type from a Python value"""
    if value is None:
        return "NULL"
    elif isinstance(value, bool):
        return "BOOL"
    elif isinstance(value, int):
        return "INT"
    elif isinstance(value, float):
        return "FLOAT"
    elif isinstance(value, dict):
        return "DICT"
    elif isinstance(value, list):
        return "LIST"
    else:
        return "STR"
