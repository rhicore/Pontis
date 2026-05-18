#!/usr/bin/env python3
"""将 bird 全局知识库导出为可读文件。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.workspace import Workspace
from tool.utils.knowledge_meta import (
    derive_knowledge_brief,
    derive_knowledge_detail,
    normalize_knowledge_meta,
)


OUTPUT_ROOT = PROJECT_ROOT / "example_data" / "bird_global"
ENTRY_DIR = OUTPUT_ROOT / "entries"
BIRD_README_PATH = PROJECT_ROOT / "scripts" / "BIRD" / "BIRD_README.md"


def _safe_name(name: str) -> str:
    text = re.sub(r"[^\w\-\.]+", "_", name.strip(), flags=re.UNICODE)
    return text or "unnamed"


def _meta_lines(node: dict) -> list[str]:
    lines = []
    labels = node.get("labels", [])
    if labels:
        lines.append(f"- labels: {', '.join(labels)}")
    brief = node.get("brief") or derive_knowledge_brief(node)
    if brief:
        lines.append(f"- brief: {brief}")
    return lines


def _fetch_neighbors(ws: Workspace, name: str) -> list[dict]:
    rows = ws.cypher(
        "MATCH (n {name: $name})--(m) RETURN m",
        params={"name": name},
        project="bird",
    )
    out = []
    for row in rows:
        m = row.get("m", {})
        out.append({
            "name": m.get("name", ""),
            "labels": m.get("labels", []),
            "brief": m.get("brief", ""),
        })
    out.sort(key=lambda x: (x["name"], x["labels"]))
    return out


def sync_bird_readme(ws: Workspace) -> None:
    """同步 scripts/BIRD/BIRD_README.md 到 bird 项目的 README 节点。"""
    detail = BIRD_README_PATH.read_text(encoding="utf-8").strip()
    brief = "BIRD 数据集跨库经验库使用说明"
    rows = ws.cypher(
        "MATCH (n {name: 'README'}) RETURN n",
        project="bird",
    )
    if rows:
        node = rows[0].get("n", {})
        ws.cypher(
            "MATCH (n {id: $id}) SET n.brief = $brief, n.detail = $detail",
            params={"id": node.get("id"), "brief": brief, "detail": detail},
            project="bird",
        )
        return
    ws.cypher(
        "CREATE (n {name: 'README', brief: $brief, detail: $detail})",
        params={"brief": brief, "detail": detail},
        project="bird",
    )


def export_bird_global() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)

    ws = Workspace(active_projects=["bird"])
    sync_bird_readme(ws)
    rows = ws.cypher("MATCH (n) RETURN n", project="bird")

    nodes = []
    for row in rows:
        n = row.get("n", {})
        n = normalize_knowledge_meta(n.get("project"), n.get("labels"), n)
        name = n.get("name", "")
        if not name:
            continue
        nodes.append(n)

    nodes.sort(key=lambda n: (n.get("name", ""), n.get("labels", [])))

    index_lines = ["# bird global export", ""]
    for node in nodes:
        name = node.get("name", "")
        labels = node.get("labels", [])
        brief = node.get("brief", "") or (derive_knowledge_brief(node) or "")
        filename = f"{_safe_name(name)}.md"
        neighbors = _fetch_neighbors(ws, name)

        lines = [f"# {name}", ""]
        lines.extend(_meta_lines(node))
        detail = node.get("detail") or derive_knowledge_detail(node, labels)
        if detail:
            lines += ["", "## detail", "", str(detail)]
        if neighbors:
            lines += ["", "## neighbors", ""]
            for item in neighbors:
                label_text = ", ".join(item.get("labels", [])) or "-"
                brief_text = item.get("brief", "")
                lines.append(f"- `{item['name']}` [{label_text}] {brief_text}".rstrip())

        (ENTRY_DIR / filename).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        label_text = ", ".join(labels) if labels else "-"
        index_lines.append(f"- [{name}](entries/{filename}) [{label_text}] {brief}".rstrip())

    (OUTPUT_ROOT / "EXPORT_INDEX.md").write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    export_bird_global()
    print(f"Exported bird global to {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
