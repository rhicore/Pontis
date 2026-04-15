"""Registry — 模块注册表与管线定义

编辑下方的 PIPELINE 列表来控制 extractor 执行哪些模块、按什么顺序。
每个元素是一个模块名，对应 extractor/modules/ 下的一个 generate() 函数。

要切换 sketch 模式：将 db_column_stats + db_column_sample + db_column_topk
替换为 db_column_sketch_stats 即可。

要跳过某个阶段：直接注释掉对应行。

Usage:
    from extractor import extract
    extract("./my_data")
"""
import logging
from pathlib import Path
from typing import Dict, List

from storage import Store
from extractor.modules._utils import load_config

logger = logging.getLogger(__name__)

# 需要传 config 参数的模块
_CONFIG_MODULES = {"db_column_overlap", "db_column_rel"}

_REGISTRY = None


def _get_registry() -> Dict[str, object]:
    """延迟导入，返回 (name → callable) 注册表。避免循环依赖。"""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    from extractor.modules.db_basic import generate as db_basic
    from extractor.modules.csv_basic import generate as csv_basic
    from extractor.modules.serialized_basic import generate as serialized_basic
    from extractor.modules.text_basic import generate as text_basic
    from extractor.modules.db_info import generate as db_info
    from extractor.modules.db_table_info import generate as db_table_info
    from extractor.modules.db_column_stats import generate as db_column_stats
    from extractor.modules.db_column_sample import generate as db_column_sample
    from extractor.modules.db_column_topk import generate as db_column_topk
    from extractor.modules.csv_info import generate as csv_info
    from extractor.modules.csv_column_stats import generate as csv_column_stats
    from extractor.modules.csv_column_sample import generate as csv_column_sample
    from extractor.modules.csv_column_topk import generate as csv_column_topk
    from extractor.modules.json_pattern import generate as json_pattern
    from extractor.modules.text_info import generate as text_info
    from extractor.modules.db_table_relations import generate as db_table_relations
    from extractor.modules.db_column_overlap import generate as db_column_overlap
    from extractor.modules.db_column_rel import generate as db_column_rel
    from extractor.modules.ai_db_summary import generate as ai_db_summary
    from extractor.modules.ai_db_table_summary import generate as ai_db_table_summary
    from extractor.modules.ai_db_column_summary import generate as ai_db_column_summary
    from extractor.modules.ai_json_summary import generate as ai_json_summary
    from extractor.modules.ai_text_summary import generate as ai_text_summary

    _REGISTRY = {
        # Phase 1 — 实体展开（同时创建文件节点）
        "db_basic": db_basic,
        "csv_basic": csv_basic,
        "serialized_basic": serialized_basic,
        "text_basic": text_basic,
        # Phase 2 — DB 信息
        "db_info": db_info,
        "db_table_info": db_table_info,
        "db_column_stats": db_column_stats,
        "db_column_sample": db_column_sample,
        "db_column_topk": db_column_topk,
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
        from extractor.modules.db_column_sketch_stats import generate as sketch
        _REGISTRY["db_column_sketch_stats"] = sketch
    except ImportError:
        pass

    return _REGISTRY


# ╔══════════════════════════════════════════════════════════════════╗
# ║  PIPELINE — 编辑这个列表来控制提取流程                          ║
# ║  注释掉 = 跳过，调换顺序 = 改变执行顺序                        ║
# ╚══════════════════════════════════════════════════════════════════╝

PIPELINE: List[str] = [
    # ── Phase 1: 实体展开（同时创建文件节点） ──
    "db_basic",
    "csv_basic",
    "serialized_basic",
    "text_basic",

    # ── Phase 2: DB 信息 ──
    "db_info",
    "db_table_info",
    "db_column_stats",
    "db_column_sample",
    "db_column_topk",
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


def run_pipeline(pipeline: List[str], store: Store, config=None) -> None:
    """按 PIPELINE 列表顺序执行模块。"""
    registry = _get_registry()

    for name in pipeline:
        if name not in registry:
            logger.warning(f"Unknown module: {name}, skipping")
            continue

        func = registry[name]
        logger.info(f"  [{name}]")

        try:
            if name in _CONFIG_MODULES:
                func(store, config=config)
            else:
                func(store)
        except Exception as e:
            logger.warning(f"Module {name} failed: {e}")


def extract(target: str, config_path: str = None, verbose: bool = False) -> None:
    """全量提取入口"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s' if not verbose else '%(levelname)s: %(message)s'
    )

    target_path = Path(target).resolve()
    if not target_path.exists():
        raise ValueError(f"Target path does not exist: {target_path}")

    config = load_config(config_path)
    store = Store(str(target_path))

    store.clear_edges()

    logger.info(f"=== Pontis Extractor: {target_path} ===")
    logger.info(f"Pipeline: {len(PIPELINE)} modules\n")

    run_pipeline(PIPELINE, store, config)

    logger.info("\n=== Extraction complete ===")
