"""Local filesystem source adapter for fs source modules."""

from __future__ import annotations

import builtins
import os


class LocalSourceAdapter:
    """Filesystem-backed source adapter for `source.type: fs` projects."""

    def __init__(self, root: str, *, exclude_paths: list[str] | None = None):
        resolved = os.path.realpath(os.path.expanduser(root)) if root else ""
        self.selected_file = os.path.basename(resolved) if resolved and os.path.isfile(resolved) else ""
        self.root = os.path.dirname(resolved) if self.selected_file else resolved
        self.exclude_paths = tuple(
            sorted({self._normalize_relative(path) for path in (exclude_paths or []) if path})
        )

    def absolute_path(self, path: str) -> str:
        if not self.root:
            raise ValueError("Source root is not configured")
        if not path or path == ".":
            return self.root
        candidate = os.path.realpath(os.path.join(self.root, path))
        if os.path.commonpath([self.root, candidate]) != self.root:
            raise ValueError(f"Path escapes source root: {path}")
        if self.selected_file and path not in {"", ".", self.selected_file}:
            raise FileNotFoundError(f"Path is outside the selected file source: {path}")
        if self._is_excluded(path):
            raise FileNotFoundError(f"Source path is excluded: {path}")
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
        if self.selected_file:
            yield "", [], [self.selected_file]
            return
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d != ".pontis"]
            rel_root = os.path.relpath(root, self.root)
            if rel_root == ".":
                rel_root = ""
            dirs[:] = [
                dirname for dirname in dirs
                if not self._is_excluded(os.path.join(rel_root, dirname))
            ]
            files = [
                filename for filename in files
                if not self._is_excluded(os.path.join(rel_root, filename))
            ]
            yield rel_root, dirs, files

    @staticmethod
    def _normalize_relative(path: str) -> str:
        normalized = os.path.normpath(str(path).replace("\\", "/"))
        return "" if normalized == "." else normalized.replace("\\", "/").strip("/")

    def _is_excluded(self, path: str) -> bool:
        rel = self._normalize_relative(path)
        return any(rel == prefix or rel.startswith(prefix + "/") for prefix in self.exclude_paths)
