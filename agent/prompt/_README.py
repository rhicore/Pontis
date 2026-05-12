"""动态层 — 将项目 README 正文注入系统提示词。"""

from __future__ import annotations

import os
import re

from storage.workspace import Workspace


_ATX_HEADING_RE = re.compile(r"^(#{1,6})(\s+.*)?$")


def _demote_markdown_headings(text: str, bump: int = 3) -> str:
    """将 README 内的 markdown 标题整体降级，避免嵌入系统提示词后层级回跳。"""
    lines: list[str] = []
    in_fence = False

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue

        m = _ATX_HEADING_RE.match(line)
        if not m:
            lines.append(line)
            continue

        hashes, rest = m.groups()
        level = min(6, len(hashes) + bump)
        lines.append("#" * level + (rest or ""))

    return "\n".join(lines)


def build_readme_context(project_path: str, spec=None) -> str:
    """读取当前激活项目的 README 节点，并拼接到系统提示词中。"""
    if spec and spec.projects:
        active = list(spec.projects)
    elif project_path:
        active = [os.path.basename(os.path.abspath(project_path))]
    else:
        active = []

    if not active:
        return ""

    try:
        ws = Workspace(project_path=project_path, active_projects=active)
    except Exception:
        return ""

    sections: list[str] = []
    for project in active:
        try:
            rows = ws.cypher("MATCH (n {name: 'README'}) RETURN n", project=project)
        except Exception:
            continue
        if not rows:
            continue
        node = rows[0].get("n") or {}
        detail = (node.get("detail") or "").strip()
        if not detail:
            continue
        detail = _demote_markdown_headings(detail, bump=3)
        sections.extend([
            f"### {project} README",
            "",
            detail,
            "",
        ])

    if not sections:
        return ""

    return "\n".join(["## 项目 README", ""] + sections).strip()
