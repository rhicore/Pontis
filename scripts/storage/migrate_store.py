"""Store 数据迁移脚本 — 将旧格式实体转换为新的 label 体系。

迁移内容：
1. 去除类型后缀：drivers.table → drivers, driverId.INT.col → driverId
2. 去除关系实体后缀：orders.user_id__to__users.id.fk → orders.user_id__to__users.id
3. _namespaces → _labels：file → file/db, knowledge → knowledge/convention 等
4. 去除 :: 前缀：db_path::entity_name → entity_name
5. 知识实体（.convention/.pattern/.term/.lesson/.example）保留后缀

用法：
    python scripts/migrate_store.py <project_path> [--dry-run]
"""
import os
import sys
import yaml
import logging
import argparse
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _infer_labels_from_name(entity_name: str, old_namespaces: List[str]) -> List[str]:
    """从旧实体名和 namespaces 推断新 labels。"""
    # 文件实体（无 :: 无类型后缀的相对路径）
    if "::" not in entity_name and not any(
        entity_name.endswith(s) for s in [
            ".table", ".view", ".col",
            ".fk", ".rel", ".overlap", ".disambig",
            ".convention", ".pattern", ".term", ".lesson", ".example",
            ".chunk", ".directory",
        ]
    ):
        # 检查是否是文件路径
        _, ext = os.path.splitext(entity_name)
        ext = ext.lower()
        file_type_map = {
            ".db": "file/db", ".sqlite": "file/db", ".sqlite3": "file/db", ".duckdb": "file/db",
            ".csv": "file/csv", ".tsv": "file/tsv",
            ".json": "file/json", ".jsonl": "file/json",
            ".yaml": "file/yaml", ".yml": "file/yaml",
            ".md": "file/text", ".txt": "file/text",
        }
        if ext in file_type_map:
            return [file_type_map[ext]]
        if old_namespaces:
            return old_namespaces  # 保持旧的
        return ["file"]

    # 提取 :: 后的实体部分
    entity_part = entity_name.split("::", 1)[1] if "::" in entity_name else entity_name

    # 表
    if entity_part.endswith(".table"):
        return ["table"]

    # 视图
    if entity_part.endswith(".view"):
        return ["view"]

    # 列: table.col.TYPE.col
    if entity_part.endswith(".col"):
        parts = entity_part.rsplit(".", 2)
        if len(parts) >= 3:
            col_type = parts[-2].upper()
            return [f"col/{col_type}"]
        return ["col"]

    # FK
    if entity_part.endswith(".fk"):
        return ["fk"]

    # Overlap
    if entity_part.endswith(".overlap"):
        return ["overlap"]

    # Rel
    if entity_part.endswith(".rel"):
        return ["rel"]

    # Disambig
    if entity_part.endswith(".disambig"):
        return ["disambig"]

    # 知识实体
    knowledge_suffixes = {
        ".convention": "knowledge/convention",
        ".pattern": "knowledge/pattern",
        ".term": "knowledge/term",
        ".lesson": "knowledge/lesson",
        ".example": "knowledge/example",
    }
    for suffix, label in knowledge_suffixes.items():
        if entity_part.endswith(suffix):
            return [label]

    # Chunk
    if entity_part.endswith(".chunk"):
        return ["chunk"]

    # Fallback: 使用旧 namespaces
    if old_namespaces:
        return old_namespaces

    return []


def _migratename(entity_name: str) -> str:
    """将旧实体名转换为新格式（去除 :: 和类型后缀）。"""
    # 无 :: 的实体（文件路径、知识实体等）
    if "::" not in entity_name:
        # 知识实体保留后缀
        knowledge_suffixes = (".convention", ".pattern", ".term", ".lesson", ".example")
        if entity_name.endswith(knowledge_suffixes):
            return entity_name
        # 关系实体去除后缀（如 orders.user_id__to__users.id.fk → orders.user_id__to__users.id）
        rel_suffixes = (".fk", ".rel", ".overlap")
        for suffix in rel_suffixes:
            if entity_name.endswith(suffix):
                return entity_name[:-len(suffix)]
        # 其他无 :: 的实体保持不变
        return entity_name

    # 有 :: 的实体
    entity_part = entity_name.split("::", 1)[1]

    # 关系实体：去除 :: 前缀和类型后缀
    rel_suffixes = (".fk", ".rel", ".overlap")
    if entity_part.endswith(rel_suffixes):
        # 去除后缀：orders.user_id__to__users.id.fk → orders.user_id__to__users.id
        for suffix in rel_suffixes:
            if entity_part.endswith(suffix):
                return entity_part[:-len(suffix)]
        return entity_part

    # Disambig 实体保留 .disambig 后缀
    if entity_part.endswith(".disambig"):
        return entity_part

    # 知识实体（不太可能有 ::，但以防万一）
    knowledge_suffixes = (".convention", ".pattern", ".term", ".lesson", ".example")
    if entity_part.endswith(knowledge_suffixes):
        return entity_part

    # 表: table.table → table
    if entity_part.endswith(".table"):
        return entity_part[:-len(".table")]

    # 视图: view.view → view
    if entity_part.endswith(".view"):
        return entity_part[:-len(".view")]

    # 列: colname.TYPE.col → colname, table.colname.TYPE.col → colname
    if entity_part.endswith(".col"):
        parts = entity_part.split(".")
        if len(parts) >= 4:
            # table.colname.TYPE.col → colname
            return parts[-3]
        elif len(parts) == 3:
            # colname.TYPE.col → colname
            return parts[0]
        return entity_part.replace(".col", "")

    # Chunk 等其他实体
    return entity_part


def migrate_store(project_path: str, dry_run: bool = False) -> None:
    """执行迁移。"""
    pontis_root = os.path.join(project_path, ".pontis")
    nodes_root = os.path.join(pontis_root, "nodes")

    if not os.path.isdir(nodes_root):
        logger.error(f"No .pontis/nodes/ found in {project_path}")
        return

    # 收集所有实体
    entities = []
    for entry in os.listdir(nodes_root):
        if not entry.startswith("ent_"):
            continue
        meta_file = os.path.join(nodes_root, entry, "_meta.yml")
        if not os.path.isfile(meta_file):
            continue
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = yaml.safe_load(f) or {}
        entities.append((entry, meta_file, meta))

    logger.info(f"Found {len(entities)} entities")

    # 检测名称冲突
    new_names: Dict[str, List[str]] = {}
    collisions = []
    for ent_id, _, meta in entities:
        old_name = meta.get("name", "")
        new_name = _migratename(old_name)
        if new_name not in new_names:
            new_names[new_name] = []
        new_names[new_name].append(ent_id)
        if len(new_names[new_name]) > 1:
            collisions.append((new_name, new_names[new_name]))

    if collisions:
        logger.warning(f"Name collisions detected ({len(collisions)}):")
        for name, ids in collisions[:10]:
            logger.warning(f"  '{name}' → {len(ids)} entities: {ids[:3]}...")

    # 迁移
    migrated = 0
    for ent_id, meta_file, meta in entities:
        old_name = meta.get("name", "")
        old_ns = meta.get("_namespaces", [])
        new_name = _migratename(old_name)
        new_labels = _infer_labels_from_name(old_name, old_ns)

        changed = False

        if old_name != new_name:
            logger.debug(f"  Rename: {old_name} → {new_name}")
            meta["name"] = new_name
            changed = True

        if old_ns and old_ns != new_labels:
            meta["_labels"] = new_labels
            if "_namespaces" in meta:
                del meta["_namespaces"]
            changed = True
        elif not old_ns and new_labels:
            meta["_labels"] = new_labels
            changed = True

        if not changed:
            continue

        migrated += 1

        if not dry_run:
            with open(meta_file, 'w', encoding='utf-8') as f:
                yaml.dump(meta, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)

    action = "Would migrate" if dry_run else "Migrated"
    logger.info(f"{action} {migrated} entities")

    if dry_run:
        logger.info("Dry run — no changes written. Use without --dry-run to apply.")


def main():
    parser = argparse.ArgumentParser(description="Migrate .pontis/ store to new label format")
    parser.add_argument("project_path", help="Project directory containing .pontis/")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    migrate_store(args.project_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
