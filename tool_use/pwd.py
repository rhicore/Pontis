"""pwd command - Print working directory

Usage:
    python -m tool_use.pwd [cwd]
"""
import sys


def pwd_command(cwd: str = "") -> str:
    """Print current working directory"""
    return f"/{cwd}" if cwd else "/"


if __name__ == "__main__":
    cwd = sys.argv[1] if len(sys.argv) > 1 else ""
    print(pwd_command(cwd))
