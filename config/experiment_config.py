"""Dependency-free loader for this method experiment_config.yaml file.

The config file intentionally uses a small YAML subset: nested mappings, lists,
scalars, and comments. This loader is enough for the experiment scripts without
adding PyYAML as another dependency across baselines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "experiment_config.yaml"


def load_experiment_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = _strip_comment(raw_line.strip())
        if not line:
            continue

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if line.startswith("- "):
            value = _parse_scalar(line[2:].strip())
            if not isinstance(parent, list):
                raise ValueError(f"List item without list parent: {raw_line}")
            parent.append(value)
            continue

        if ":" not in line:
            raise ValueError(f"Unsupported config line: {raw_line}")

        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            parent[key] = _parse_scalar(raw_value)
            continue

        child = _new_child(lines, raw_line)
        parent[key] = child
        stack.append((indent, child))

    return root


def _new_child(lines: list[str], current_line: str) -> Any:
    current_index = lines.index(current_line)
    current_indent = len(current_line) - len(current_line.lstrip(" "))
    for next_line in lines[current_index + 1 :]:
        if not next_line.strip() or next_line.lstrip().startswith("#"):
            continue
        next_indent = len(next_line) - len(next_line.lstrip(" "))
        if next_indent <= current_indent:
            return {}
        return [] if next_line.strip().startswith("- ") else {}
    return {}


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index].rstrip()
    return line


def _parse_scalar(value: str) -> Any:
    if value in {"null", "None", "~"}:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


if __name__ == "__main__":
    import json

    print(json.dumps(load_experiment_config(), ensure_ascii=False, indent=2))
