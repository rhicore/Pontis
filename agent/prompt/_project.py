"""动态层 — 运行时生成的项目上下文信息。"""
import os

import yaml

from storage.config import load_config


def build_project_context(project_path: str, spec=None) -> str:
    """构建动态项目上下文：只显示本次开启的项目及统计。"""
    config = load_config(project_path=project_path)

    # 确定当前项目列表
    if spec and spec.projects:
        active = spec.projects
    else:
        active = [os.path.basename(os.path.abspath(project_path))]

    parts = ["## 当前项目"]

    for name in active:
        path = config.resolve_source_path(name)
        parts.append(f"### {name}")
        if path:
            parts.append(f"- 路径: {path}")
            overview = _get_project_overview(path)
            if overview:
                parts.append(overview)
        else:
            graph_path = config.resolve_graph_path(name)
            parts.append("- 类型: graph-only project")
            if graph_path:
                parts.append(f"- 图存储: {graph_path}")

    return "\n".join(parts)


def _get_project_overview(project_path: str) -> str:
    """扫描项目的 .pontis 目录，按标签统计实体。"""
    if not project_path:
        return ""

    pontis_path = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_path):
        return ""

    lines = []
    nodes_dir = os.path.join(pontis_path, "nodes")
    label_counts = {}

    if os.path.exists(nodes_dir):
        for entry in os.listdir(nodes_dir):
            if not entry.startswith("ent_"):
                continue
            meta_path = os.path.join(nodes_dir, entry, "_meta.yml")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path, 'r') as f:
                    raw = yaml.safe_load(f) or {}
            except Exception:
                continue

            labels = raw.get("_labels", [])
            for lbl in labels:
                label_counts[lbl] = label_counts.get(lbl, 0) + 1

    if label_counts:
        parts = [f"{k}({v})" for k, v in sorted(label_counts.items())]
        lines.append(f"- 实体: {', '.join(parts)}")

    edges_path = os.path.join(pontis_path, "_edges.yml")
    if os.path.exists(edges_path):
        try:
            with open(edges_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            edge_count = len(data.get("edges", []))
            if edge_count:
                lines.append(f"- 关系边: {edge_count}")
        except Exception:
            pass

    return "\n".join(lines)
