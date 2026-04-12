"""Pipeline — 提取管线定义

编辑下方的 PIPELINE 列表来控制 extractor 执行哪些模块、按什么顺序。
每个元素是一个模块名，对应 extractor/ 下的一个 generate() 函数。

要切换 sketch 模式：将 db_column_stats + db_column_sample + db_column_topk
替换为 db_column_sketch_stats 即可。

要跳过某个阶段：直接注释掉对应行。
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# 需要传 config 参数的模块
_CONFIG_MODULES = {"db_column_overlap", "db_column_rel"}

# skeleton 签名特殊: (target_path, storage, config)
_SKELETON_MODULE = "skeleton"

_REGISTRY = None


def _get_registry() -> Dict[str, object]:
    """延迟导入，返回 (name → callable) 注册表。避免循环依赖。"""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    from extractor.skeleton import generate_skeleton
    from extractor.db_basic import generate as db_basic
    from extractor.csv_basic import generate as csv_basic
    from extractor.serialized_basic import generate as serialized_basic
    from extractor.text_basic import generate as text_basic
    from extractor.db_info import generate as db_info
    from extractor.db_table_info import generate as db_table_info
    from extractor.db_column_stats import generate as db_column_stats
    from extractor.db_column_sample import generate as db_column_sample
    from extractor.db_column_topk import generate as db_column_topk
    from extractor.csv_info import generate as csv_info
    from extractor.csv_column_stats import generate as csv_column_stats
    from extractor.csv_column_sample import generate as csv_column_sample
    from extractor.csv_column_topk import generate as csv_column_topk
    from extractor.json_pattern import generate as json_pattern
    from extractor.text_info import generate as text_info
    from extractor.db_table_relations import generate as db_table_relations
    from extractor.db_column_overlap import generate as db_column_overlap
    from extractor.db_column_rel import generate as db_column_rel
    from extractor.ai_db_summary import generate as ai_db_summary
    from extractor.ai_db_table_summary import generate as ai_db_table_summary
    from extractor.ai_db_column_summary import generate as ai_db_column_summary
    from extractor.ai_json_summary import generate as ai_json_summary
    from extractor.ai_text_summary import generate as ai_text_summary

    _REGISTRY = {
        # Phase 1
        "skeleton": generate_skeleton,
        # Phase 1.5 — 实体展开
        "db_basic": db_basic,
        "csv_basic": csv_basic,
        "serialized_basic": serialized_basic,
        "text_basic": text_basic,
        # Phase 2 — DB 信息
        "db_info": db_info,
        "db_table_info": db_table_info,
        "db_column_stats": db_column_stats,           # 精确统计（小表）
        "db_column_sample": db_column_sample,          # 精确采样
        "db_column_topk": db_column_topk,              # 精确 top-K
        # "db_column_sketch_stats": None,              # Sketch 近似统计（大表），取消注释启用
        # Phase 3 — CSV 信息
        "csv_info": csv_info,
        "csv_column_stats": csv_column_stats,
        "csv_column_sample": csv_column_sample,
        "csv_column_topk": csv_column_topk,
        # Phase 4-5
        "json_pattern": json_pattern,
        "text_info": text_info,
        # Phase 6-8 — 关系检测
        "db_table_relations": db_table_relations,
        "db_column_overlap": db_column_overlap,
        "db_column_rel": db_column_rel,
        # Phase 9 — AI 总结
        "ai_db_summary": ai_db_summary,
        "ai_db_table_summary": ai_db_table_summary,
        "ai_db_column_summary": ai_db_column_summary,
        "ai_json_summary": ai_json_summary,
        "ai_text_summary": ai_text_summary,
    }

    # 尝试注册 sketch 模块
    try:
        from extractor.db_column_sketch_stats import generate as db_column_sketch_stats
        _REGISTRY["db_column_sketch_stats"] = db_column_sketch_stats
    except ImportError:
        pass

    return _REGISTRY


# ╔══════════════════════════════════════════════════════════════════╗
# ║  PIPELINE — 编辑这个列表来控制提取流程                          ║
# ║  注释掉 = 跳过，调换顺序 = 改变执行顺序                        ║
# ╚══════════════════════════════════════════════════════════════════╝

PIPELINE: List[str] = [
    # ── Phase 1: 骨架 ──
    "skeleton",

    # ── Phase 1.5: 实体展开 ──
    "db_basic",
    "csv_basic",
    "serialized_basic",
    "text_basic",

    # ── Phase 2: DB 信息 ──
    "db_info",
    "db_table_info",
    # 精确统计（适合小表，全量扫描）:
    "db_column_stats",
    "db_column_sample",
    "db_column_topk",
    # Sketch 近似统计（适合大表，单次流式扫描）:
    # 替换上面三行为下面这一行即可:
    # "db_column_sketch_stats",

    # ── Phase 3: CSV 信息 ──
    "csv_info",
    "csv_column_stats",
    "csv_column_sample",
    "csv_column_topk",

    # ── Phase 4: 序列化文件 ──
    "json_pattern",

    # ── Phase 5: 文本文件 ──
    "text_info",

    # ── Phase 6-8: 关系检测 ──
    "db_table_relations",
    "db_column_overlap",
    "db_column_rel",

    # ── Phase 9: AI 总结 ──
    "ai_db_summary",
    "ai_db_table_summary",
    "ai_db_column_summary",
    "ai_json_summary",
    "ai_text_summary",
]


def run_pipeline(pipeline: List[str], storage,
                 target_path: str = None, config=None) -> None:
    """按 PIPELINE 列表顺序执行模块。"""
    registry = _get_registry()

    for name in pipeline:
        if name not in registry:
            logger.warning(f"Unknown module: {name}, skipping")
            continue

        func = registry[name]
        logger.info(f"  [{name}]")

        try:
            if name == _SKELETON_MODULE:
                func(target_path, storage, config)
            elif name in _CONFIG_MODULES:
                func(storage, config=config)
            else:
                func(storage)
        except Exception as e:
            logger.warning(f"Module {name} failed: {e}")
