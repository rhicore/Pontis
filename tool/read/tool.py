"""Read tool — read a line range from a text file through storage handles."""

from __future__ import annotations

from tool.utils.workspace_access import resolve_file_sources


MAX_READ_LINES = 500
MAX_OUTPUT_CHARS = 24_000


def _clamp_range(start_line: int, end_line: int) -> tuple[int, int]:
    start = max(1, int(start_line or 1))
    end = max(start, int(end_line or start))
    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1
    return start, end


def read_command(
    workspace,
    ref: str = "",
    start_line: int = 1,
    end_line: int | None = None,
    current_cwd: str = "",
) -> str:
    """Read text file lines using the file node's open_file handle."""
    selector = (ref or "").strip()
    if not selector:
        return "Error: missing required field 'ref'"

    if end_line is None:
        end_line = int(start_line or 1) + 119
    start, end = _clamp_range(start_line, end_line)

    sources = resolve_file_sources(
        workspace,
        selector,
        labels=("text",),
        current_cwd=current_cwd,
        allow_directory=False,
    )
    if not sources:
        return f"Error: text file not found or not readable via storage: {selector}"
    if len(sources) > 1:
        options = "\n".join(f"- {src.path}" for src in sources[:20])
        return f"Error: ambiguous text file ref: {selector}\n{options}"

    src = sources[0]
    lines = []
    truncated_chars = False
    try:
        with src.open_file("r", encoding="utf-8", errors="ignore") as fh:
            for lineno, line in enumerate(fh, 1):
                if lineno < start:
                    continue
                if lineno > end:
                    break
                lines.append(f"{lineno} | {line.rstrip()}")
                if sum(len(item) + 1 for item in lines) > MAX_OUTPUT_CHARS:
                    truncated_chars = True
                    break
    except Exception as exc:
        return f"Error reading {src.path}: {type(exc).__name__}: {exc}"

    header = f"{src.path}:L{start}-L{end}"
    if not lines:
        return f"{header}\n(no lines in requested range)"

    result = header + "\n" + "\n".join(lines)
    if truncated_chars:
        result += "\n... (truncated by output size)"
    if end - start + 1 >= MAX_READ_LINES:
        result += f"\n... (line range limited to {MAX_READ_LINES} lines)"
    return result


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m tool.read.tool <project_name> <json_params> [cwd]")
        sys.exit(1)

    from storage.workspace import Workspace

    ws = Workspace(active_projects=[sys.argv[1]])
    _params = json.loads(sys.argv[2])
    _cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(read_command(ws, **_params, current_cwd=_cwd))
