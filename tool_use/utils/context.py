"""Tool execution context"""
import os


class ToolContext:
    """Context for tool execution, maintaining current working directory"""

    def __init__(self, pontis_root: str, cwd: str = ""):
        from tool_use.utils.vfs import PontisVFS
        self.vfs = PontisVFS(pontis_root)
        self.cwd = cwd

    def resolve_path(self, path: str) -> str:
        """Resolve a path relative to current working directory"""
        if not path or path == ".":
            return self.cwd
        if path.startswith("/"):
            return path[1:]
        return os.path.normpath(os.path.join(self.cwd, path))
