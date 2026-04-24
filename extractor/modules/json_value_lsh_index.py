"""JSON Value LSH Index — 为 JSON 文件的原始值构建 LSH 索引

遍历 JSON 树，收集所有原始值（String, Number, Bool, NULL）和 key，
为每个 JSON 文件构建统一索引。索引文件存储在 .pontis/cache/，
索引信息记录在 JSON 文件节点的 meta 中。
"""
import json
import os
import logging

from storage import Store
from extractor.modules.utils.lsh_index import LSHIndexWriter

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

    writer = LSHIndexWriter(num_buckets=256, is_numeric=False, top_k=100)

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for key, val in obj.items():
                child = f"{path}.{key}" if path else key
                writer.add_value(key)
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

    # 存储索引文件到 .pontis/cache/lsh/
    ent_id = store.resolve_ref(json_ref)[0]
    cache_file = store.cache_path("lsh", f"{ent_id}.lsh")

    writer.write(cache_file)

    store.set_meta(json_ref, {
        "_index": {
            "version": 1,
            "buckets": writer.num_buckets,
            "distinct": writer._distinct_count,
        },
    })


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
            # 已有索引则跳过
            meta = store._get_stored_meta(ref) or {}
            if meta.get("_index"):
                continue
            try:
                _build_index(ref, store)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to index {ref}: {e}")

    logger.info(f"  Indexed {count} JSON files")
