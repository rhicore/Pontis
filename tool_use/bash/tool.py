"""
Bash tool - Execute shell commands.

Simple pass-through for shell command execution.
Used as a last resort when other tools can't accomplish the task.

Parameters:
    command: Shell command string to execute
    timeout: Timeout in milliseconds (default 120000, max 600000)
"""
import os
import sys
import subprocess
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000


def bash_command(
    command: str,
    cwd: Optional[str] = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> str:
    """
    Execute a shell command.

    Args:
        command: Shell command to execute
        cwd: Working directory (defaults to project root)
        timeout_ms: Timeout in milliseconds

    Returns:
        Command output (stdout + stderr)
    """
    timeout_s = min(timeout_ms, MAX_TIMEOUT_MS) / 1000.0

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=cwd,
        )

        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.append(result.stderr)

        output = '\n'.join(output_parts) if output_parts else "(no output)"

        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"

        return output

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout_s:.0f}s"
    except Exception as e:
        return f"Error executing command: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tool_use.bash.tool <command> [cwd] [timeout_ms]")
        sys.exit(1)

    _cmd = sys.argv[1]
    _cwd = sys.argv[2] if len(sys.argv) > 2 else None
    _timeout = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_TIMEOUT_MS
    print(bash_command(_cmd, _cwd, _timeout))
