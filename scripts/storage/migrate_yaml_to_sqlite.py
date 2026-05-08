"""迁移 .pontis/ 从 YAML 格式到 SQLite 格式。

读取:
  .pontis/nodes/{ent_id}/_meta.yml → 每个实体一个 YAML 文件
  .pontis/_edges.yml → 所有边

写入:
  .pontis/store.db → SQLite 数据库（nodes + edges 表）
"""
import argparse
import json
import os
import sqlite3
import sys

import yaml


def migrate(project_path: str):
    pontis_root = os.path.join(project_path, ".pontis")
    nodes_dir = os.path.join(pontis_root, "nodes")
    edges_file = os.path.join(pontis_root, "_edges.yml")
    db_path = os.path.join(pontis_root, "store.db")

    if not os.path.isdir(nodes_dir) and not os.path.isfile(edges_file):
        print("No YAML data found, nothing to migrate.")
        return

    if os.path.exists(db_path):
        print(f"store.db already exists at {db_path}, skipping.")
        return

    # 创建 SQLite 数据库
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            props TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            a_id TEXT NOT NULL,
            b_id TEXT NOT NULL,
            PRIMARY KEY (a_id, b_id)
        )
    """)

    # 迁移节点
    node_count = 0
    if os.path.isdir(nodes_dir):
        for entry in os.listdir(nodes_dir):
            if not entry.startswith("ent_"):
                continue
            meta_file = os.path.join(nodes_dir, entry, "_meta.yml")
            if not os.path.isfile(meta_file):
                continue
            with open(meta_file, 'r', encoding='utf-8') as f:
                raw = yaml.safe_load(f)
            if raw is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO nodes (id, props) VALUES (?, ?)",
                    (entry, json.dumps(raw, ensure_ascii=False, default=str)))
                node_count += 1

    # 迁移边
    edge_count = 0
    if os.path.isfile(edges_file):
        with open(edges_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        for e in data.get("edges", []):
            nodes = e.get("nodes", [])
            if len(nodes) == 2:
                conn.execute(
                    "INSERT OR IGNORE INTO edges (a_id, b_id) VALUES (?, ?)",
                    (nodes[0], nodes[1]))
                conn.execute(
                    "INSERT OR IGNORE INTO edges (a_id, b_id) VALUES (?, ?)",
                    (nodes[1], nodes[0]))
                edge_count += 1

    conn.commit()
    conn.close()

    print(f"Migrated {node_count} nodes, {edge_count} edges → {db_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate .pontis/ from YAML to SQLite")
    parser.add_argument("project_path", help="Project directory containing .pontis/")
    args = parser.parse_args()
    migrate(args.project_path)
