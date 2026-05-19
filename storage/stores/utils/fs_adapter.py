"""Local filesystem source adapter for fs source modules."""

from __future__ import annotations

import builtins
import os


class LocalSourceAdapter:
    """Filesystem-backed source adapter for `source.type: fs` projects."""

    def __init__(self, root: str):
        self.root = os.path.realpath(os.path.expanduser(root)) if root else ""

    def absolute_path(self, path: str) -> str:
        if not self.root:
            raise ValueError("Source root is not configured")
        if not path or path == ".":
            return self.root
        candidate = os.path.realpath(os.path.join(self.root, path))
        if os.path.commonpath([self.root, candidate]) != self.root:
            raise ValueError(f"Path escapes source root: {path}")
        return candidate

    def exists(self, path: str) -> bool:
        return os.path.exists(self.absolute_path(path))

    def is_file(self, path: str) -> bool:
        return os.path.isfile(self.absolute_path(path))

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(self.absolute_path(path))

    def stat(self, path: str):
        return os.stat(self.absolute_path(path))

    def listdir(self, path: str = "") -> list[str]:
        return os.listdir(self.absolute_path(path or "."))

    def open(self, path: str, *args, **kwargs):
        return builtins.open(self.absolute_path(path), *args, **kwargs)

    def walk(self):
        if not self.root:
            return
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d != ".pontis"]
            rel_root = os.path.relpath(root, self.root)
            if rel_root == ".":
                rel_root = ""
            yield rel_root, dirs, files
