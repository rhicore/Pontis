"""cd command - Change directory

Usage:
    python -m tool_use.cd <pontis_path> <directory>
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_use.utils.context import ToolContext


def cd_command(pontis_root: str, current_cwd: str, path: str) -> str:
    """Change directory, return new cwd"""
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found: {pontis_root}"

    try:
        ctx = ToolContext(pontis_root)
        ctx.cwd = current_cwd

        resolved_path = ctx.resolve_path(path)
        full_path = os.path.join(ctx.vfs.pontis_root, resolved_path)

        if not os.path.exists(full_path):
            # Try as serialized path
            try:
                node = ctx.vfs.get_node_info(resolved_path)
                if hasattr(node, 'has_children') and node.has_children:
                    return resolved_path
                else:
                    return f"Error: '{path}' has no children"
            except:
                return f"Error: Path not found: {path}"

        if not os.path.isdir(full_path):
            return f"Error: Not a directory: {path}"

        return resolved_path
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.cd <pontis_path> <directory>")
        sys.exit(1)

    pontis_path = sys.argv[1]
    directory = sys.argv[2]
    current_cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(cd_command(pontis_path, current_cwd, directory))
