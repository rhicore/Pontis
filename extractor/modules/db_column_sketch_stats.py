"""DB Column Sketch Stats — 基于 datasketches 的近似列统计

单次流式扫描完成所有统计，常量内存，适用于百万行级 DB。

替代 db_column_stats + db_column_sample + db_column_topk 三个模块，
产出字段完全一致，下游工具无感。

Sketch 算法:
  cardinality  → HyperLogLog    (~1-2% 误差)
  min/max      → KLL Quantiles  (数值列, 误差极小)
  mean         → 流式累加        (几乎精确)
  topk         → FrequentItems  (高频值准确)
  sample       → Reservoir Sampling (随机均匀)

独立执行:
    python -m extractor.db_column_sketch_stats ./my_data
"""
import os
import random
import logging
from typing import Optional, List, Dict, Any

from storage import Store

logger = logging.getLogger(__name__)

# 流式扫描的 batch size
_FETCH_SIZE = 10000


def generate(store: Store, config=None) -> None:
    """为所有 .col 节点生成 sketch 统计。"""
    logger.info("=== Generating DB column sketch statistics ===")

    sample_size = config.sample_size if config else 20
    top_k = config.top_k if config else 5

    for ref in store.find_nodes("*.db::*.*.*.col"):
        try:
            _generate_for_column(ref, store, sample_size, top_k)
        except Exception as e:
            logger.warning(f"Failed to generate sketch stats for {ref}: {e}")


def _generate_for_column(ref: str, store: Store,
                         sample_size: int, top_k: int) -> bool:
    """为单个列生成 sketch 统计。"""
    path, entity_name = ref.split("::", 1)
    meta = store.get_meta(ref)
    if not meta:
        return False

    # 幂等：已有 cardinality 则跳过
    if "cardinality" in meta:
        return False

    # 解析实体名 → db_path, table, column, dtype
    col_parts = entity_name.replace(".col", "").split(".")
    if len(col_parts) < 3:
        return False

    table_name = col_parts[0]
    col_name = col_parts[1]
    data_type = col_parts[2]

    db_path = os.path.join(store.project_path, store.get_meta(path).get("path", ""))
    if not db_path:
        return False

    # 单次流式扫描 + sketch
    stats = _sketch_column(db_path, table_name, col_name, data_type, sample_size, top_k)
    if not stats:
        return False

    store.set_meta(ref, stats)
    logger.info(f"  Sketch stats: {ref} "
                f"(cardinality≈{stats.get('cardinality')})")
    return True


def _sketch_column(db_path: str, table: str, column: str, dtype: str,
                   sample_size: int, top_k: int) -> Optional[dict]:
    """单次流式扫描，用 sketch 计算所有统计。"""
    import sqlite3
    from datasketches import (hll_sketch, kll_floats_sketch,
                              frequent_strings_sketch, frequent_items_error_type)

    hll = hll_sketch(12)  # lg2(k)=12, ~1.5% 误差

    reservoir: List[Any] = []  # 蓄水池采样
    null_count = 0
    total = 0
    freq = frequent_strings_sketch(top_k)  # topk 频率

    # 数值列额外状态
    is_numeric = dtype in ("INT", "INTEGER", "REAL", "FLOAT")
    kll = kll_floats_sketch() if is_numeric else None
    sum_val = 0.0
    count_val = 0

    # 文本列额外状态
    is_text = dtype in ("TEXT", "VARCHAR", "CHAR")
    min_len = float('inf')
    max_len = 0
    sum_len = 0
    count_len = 0

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f'SELECT "{column}" FROM "{table}"')

        while True:
            rows = cursor.fetchmany(_FETCH_SIZE)
            if not rows:
                break

            for (val,) in rows:
                total += 1

                if val is None:
                    null_count += 1
                    continue

                non_null_count = total - null_count
                val_str = str(val)

                # HLL cardinality
                hll.update(val_str)

                # Reservoir sampling
                _reservoir_update(reservoir, val, sample_size, non_null_count)

                # TopK frequency
                freq.update(val_str)

                # 类型特定统计
                if is_numeric:
                    try:
                        fval = float(val)
                        if kll is not None:
                            kll.update(fval)
                        sum_val += fval
                        count_val += 1
                    except (ValueError, TypeError):
                        pass

                if is_text:
                    length = len(val_str)
                    min_len = min(min_len, length)
                    max_len = max(max_len, length)
                    sum_len += length
                    count_len += 1

        conn.close()

    except Exception as e:
        logger.debug(f"Sketch scan failed: {e}")
        return None

    if total == 0:
        return {"cardinality": 0, "null_count": 0}

    # 构建输出
    stats = {
        "cardinality": round(hll.get_estimate()),
        "null_count": null_count,
        "null_percentage": round((null_count / total) * 100, 2),
    }

    # 数值列统计
    if is_numeric and count_val > 0 and kll is not None:
        stats["min_value"] = kll.get_min_value()
        stats["max_value"] = kll.get_max_value()
        stats["mean_value"] = round(sum_val / count_val, 4)

    # 文本列统计
    if is_text and count_len > 0:
        stats["min_length"] = int(min_len)
        stats["max_length"] = int(max_len)
        stats["avg_length"] = round(sum_len / count_len, 2)

    # Sample
    stats["sample"] = [_format_value(v) for v in reservoir]

    # TopK — frequent_strings_sketch 返回 (value, lb, est, ub) 元组
    non_null_total = total - null_count
    freq_items = freq.get_frequent_items(frequent_items_error_type.NO_FALSE_POSITIVES)
    stats["topk"] = [
        {
            "value": item[0],
            "count": item[2],  # estimate
            "percentage": round((item[2] / non_null_total) * 100, 2) if non_null_total else 0,
        }
        for item in freq_items[:top_k]
    ]

    return stats


def _reservoir_update(reservoir: list, value, max_size: int, seen_count: int):
    """蓄水池采样。"""
    if seen_count <= max_size:
        reservoir.append(value)
    else:
        j = random.randint(0, seen_count - 1)
        if j < max_size:
            reservoir[j] = value


def _format_value(value) -> Any:
    """格式化输出值（bytes → 描述字符串）。"""
    if isinstance(value, bytes):
        return f"<BLOB:{len(value)}bytes>"
    return value
