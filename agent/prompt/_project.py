"""动态层 — 运行时生成的项目上下文信息。"""
import os

import yaml


def build_project_context(project_path: str) -> str:
    """构建动态项目上下文：项目路径 + 实体/边统计。"""
    pontis_path = os.path.join(project_path, ".pontis")
    overview = _get_project_overview(pontis_path)
    return f"## 当前项目\n- 项目路径: {project_path}\n\n{overview}"


def _get_project_overview(pontis_path: str) -> str:
    """扫描 .pontis 目录，按 _labels 统计实体类型。"""
    if not os.path.exists(pontis_path):
        return "(无 .pontis 目录，请先运行 extractor)"

    lines = ["## 数据概览"]

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
            # 用首个标签的首段作为分类 key
            if labels:
                key = labels[0].split("/")[0]
            else:
                ename = raw.get("_entity_name", "")
                if "." in ename:
                    key = ename.rsplit(".", 1)[-1]
                else:
                    key = "other"
            label_counts[key] = label_counts.get(key, 0) + 1

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
