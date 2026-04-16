"""JSON Value LSH Index — 为 JSON 文件的原始值构建 LSH 索引

遍历 JSON 树，收集所有原始值（String, Number, Bool, NULL）和 key，
为每个 JSON 文件构建统一索引。通过 store.create_file() 分配路径并注册 KG 节点。
"""
import json
import os
import logging

from storage import Store
from extractor.modules._lsh_index import LSHIndexWriter

logger = logging.getLogger(__name__)


def _build_index(json_ref: str, store: Store) -> None:
    """为单个 JSON 文件构建值索引。"""
    meta = store._get_stored_meta(json_ref)
    if not meta:
        return
    json_file = meta.get('path', json_ref)
    abs_path = os.path.join(store.project_path, json_file)
    if not os.path.isfile(abs_path):
        return

    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read {json_ref}: {e}")
        return

    # 单一索引覆盖所有原始值
    writer = LSHIndexWriter(num_buckets=256, is_numeric=False, top_k=100)

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for key, val in obj.items():
                child = f"{path}.{key}" if path else key
                writer.add_value(key)  # key 也可检索
                if isinstance(val, (str, int, float, bool)) or val is None:
                    writer.add_value(val)
                elif isinstance(val, (dict, list)):
                    walk(val, child)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                child = f"{path}[{i}]"
                if isinstance(item, (str, int, float, bool)) or item is None:
                    writer.add_value(item)
                elif isinstance(item, (dict, list)):
                    walk(item, child)

    walk(data)

    idx_ref = json_ref + "::values.idx"
    file_path = store.create_file(
        ref=idx_ref,
        meta={
            "index_version": 1,
            "index_buckets": writer.num_buckets,
            "index_distinct": writer._distinct_count,
        },
        parent_ref=json_ref,
    )
    writer.write(file_path)


def generate(store: Store) -> None:
    """为所有 JSON 文件构建值索引。"""
    logger.info("=== Generating JSON value LSH indexes ===")

    count = 0
    for pattern in ["**/*.json", "**/*.jsonl"]:
        for ref in store.find_nodes(pattern):
            if "::" in ref:
                continue
            if ref.endswith('.jsonl'):
                continue
            idx_refs = store.find_connected(ref, edge_type="contains", pattern="*.idx")
            if idx_refs:
                continue
            try:
                _build_index(ref, store)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to index {ref}: {e}")

    logger.info(f"  Indexed {count} JSON files")
