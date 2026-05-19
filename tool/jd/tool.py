"""jd tool — browse JSON files through a JSON-VFS path."""

from __future__ import annotations

import json
from urllib.parse import quote, unquote

from tool.utils.workspace_access import resolve_file_sources


DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_VALUE_CHARS = 120


def _split_vfs_path(path: str) -> tuple[str, list[str]]:
    if "#" in path:
        file_path, inner = path.split("#", 1)
    else:
        file_path, inner = path, ""
    inner = inner.strip()
    if inner.startswith("/"):
        inner = inner[1:]
    if not inner:
        return file_path, []
    return file_path, [unquote(part) for part in inner.split("/") if part != ""]


def _join_child_path(file_path: str, parent: list[str], child: str) -> str:
    parts = parent + [child]
    encoded = "/".join(quote(str(part), safe="") for part in parts)
    return f"{file_path}#/{encoded}"


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _preview(value, max_chars: int) -> str:
    if isinstance(value, dict):
        return f"{len(value)} pairs"
    if isinstance(value, list):
        return f"{len(value)} items"
    if value is None:
        return "null"
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars - 3] + "..."
    return text


def _navigate(data, segments: list[str]):
    value = data
    walked = []
    for seg in segments:
        walked.append(seg)
        if isinstance(value, dict):
            if seg not in value:
                return None, f"JSON path not found: /{'/'.join(walked)}"
            value = value[seg]
        elif isinstance(value, list):
            try:
                idx = int(seg)
            except ValueError:
                return None, f"Expected list index at /{'/'.join(walked)}"
            if idx < 0 or idx >= len(value):
                return None, f"List index out of range at /{'/'.join(walked)}"
            value = value[idx]
        else:
            return None, f"Scalar value has no child path at /{'/'.join(walked[:-1]) or '/'}"
    return value, ""


def _schema_summary(items: list) -> str:
    dict_items = [item for item in items if isinstance(item, dict)]
    if not dict_items:
        return ""
    keys = []
    seen = set()
    for item in dict_items[:100]:
        for key in item.keys():
            if key not in seen:
                seen.add(key)
                keys.append(key)
            if len(keys) >= 20:
                break
        if len(keys) >= 20:
            break
    suffix = "..." if len(seen) > len(keys) else ""
    return f"schema keys: {', '.join(keys)}{suffix}"


def _format_children(file_path: str, parent: list[str], value, limit: int, offset: int, max_value_chars: int) -> str:
    lines = []
    current = f"{file_path}#/{'/'.join(quote(str(p), safe='') for p in parent)}" if parent else file_path
    lines.append(f"{current} ({_type_name(value)})")

    if isinstance(value, dict):
        items = list(value.items())
        total = len(items)
        page = items[offset:offset + limit]
        lines.append("key | type | preview | path")
        lines.append("--- | --- | --- | ---")
        for key, child in page:
            lines.append(
                f"{key} | {_type_name(child)} | {_preview(child, max_value_chars)} | "
                f"{_join_child_path(file_path, parent, str(key))}"
            )
    elif isinstance(value, list):
        total = len(value)
        page = list(enumerate(value))[offset:offset + limit]
        summary = _schema_summary(value)
        if summary:
            lines.append(summary)
        lines.append("index | type | preview | path")
        lines.append("--- | --- | --- | ---")
        for idx, child in page:
            lines.append(
                f"{idx} | {_type_name(child)} | {_preview(child, max_value_chars)} | "
                f"{_join_child_path(file_path, parent, str(idx))}"
            )
    else:
        return f"{current} ({_type_name(value)})\n{_preview(value, max_value_chars)}"

    if total == 0:
        lines.append("(empty)")
    shown_to = min(offset + limit, total)
    lines.append(f"\nShowing {offset}-{shown_to} of {total}.")
    if shown_to < total:
        lines.append(f"Next page: jd(path=\"{current}\", offset={shown_to}, limit={limit})")
    return "\n".join(lines)


def jd_command(
    workspace,
    path: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    max_value_chars: int = MAX_VALUE_CHARS,
) -> str:
    """Browse a JSON file or a JSON inner path."""
    if not path:
        return "Error: missing required field 'path'"

    file_path, segments = _split_vfs_path(path)
    if not file_path:
        return "Error: missing JSON file path before '#'"

    sources = resolve_file_sources(workspace, file_path, labels=("json",), allow_directory=False)
    if not sources and file_path.lower().endswith(".json"):
        sources = resolve_file_sources(workspace, file_path, allow_directory=False)
        sources = [src for src in sources if "json" in src.labels or src.path.lower().endswith(".json")]

    if not sources:
        return f"Error: JSON file not found or not readable via storage: {file_path}"
    if len(sources) > 1:
        options = "\n".join(f"- {src.path}" for src in sources[:20])
        return f"Error: ambiguous JSON file path: {file_path}\n{options}"

    src = sources[0]
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    max_value_chars = max(20, min(int(max_value_chars or MAX_VALUE_CHARS), 1000))

    try:
        with src.open_file("r", encoding="utf-8", errors="ignore") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return f"Error: invalid JSON in {src.path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
    except Exception as exc:
        return f"Error reading {src.path}: {type(exc).__name__}: {exc}"

    value, err = _navigate(data, segments)
    if err:
        return f"Error: {err}"

    return _format_children(src.path, segments, value, limit, offset, max_value_chars)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m tool.jd.tool <project_name> <json_params>")
        sys.exit(1)

    from storage.workspace import Workspace

    ws = Workspace(active_projects=[sys.argv[1]])
    _params = json.loads(sys.argv[2])
    print(jd_command(ws, **_params))
