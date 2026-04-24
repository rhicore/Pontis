"""Build Cache — 将扁平存储转换为树状目录结构用于可视化

在正常 extract 之后执行，把 .pontis/nodes/ 扁平存储转换为
.pontis/_cache/ 下的树状目录结构，和之前路径镜像的展示逻辑一致。

用法：
    python -m utils.scripts.build_cache <project_path>
"""
import os
import shutil
import sys

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import yaml

from storage import Store


def build_cache(project_path: str):
    store = Store(project_path)
    if not store.pontis_exists:
        print(f"No .pontis/ found in {project_path}")
        return

    cache_root = os.path.join(store._pontis_root, "_cache")

    # 清空旧缓存
    if os.path.exists(cache_root):
        shutil.rmtree(cache_root)
    os.makedirs(cache_root, exist_ok=True)

    store._ensure_index()

    for ent_id, ref in store._id_index.items():
        raw = store._read_yaml(store._node_meta_path(ent_id))
        if raw is None:
            continue

        entity_name = raw.get("_entity_name", "")

        if entity_name:
            files = raw.get("_files", [])
            parent_path = files[0] if files else ""
            if not parent_path:
                continue
            cache_meta = os.path.join(
                cache_root, parent_path, "_entity", entity_name, "_meta.yml"
            )
        else:
            path = raw.get("path", "")
            if not path:
                continue
            cache_meta = os.path.join(cache_root, path, "_meta.yml")

        os.makedirs(os.path.dirname(cache_meta), exist_ok=True)
        with open(cache_meta, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # 边文件：转换为 ref 格式便于阅读
    raw_edges = store._read_edges_raw()
    if raw_edges:
        readable_edges = []
        for e in raw_edges:
            readable_edges.append({
                "from": store._resolve_edge_ref(e.get("from", "")),
                "type": e.get("type", ""),
                "to": store._resolve_edge_ref(e.get("to", "")),
            })
        with open(os.path.join(cache_root, "_edges.yml"), "w",
                   encoding="utf-8") as f:
            yaml.dump({"edges": readable_edges}, f,
                      default_flow_style=False, allow_unicode=True)

    # 统计
    node_count = len(store._id_index)
    edge_count = len(raw_edges)
    print(f"Cache built: {cache_root} ({node_count} nodes, {edge_count} edges)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m utils.scripts.build_cache <project_path>")
        sys.exit(1)
    build_cache(sys.argv[1])
