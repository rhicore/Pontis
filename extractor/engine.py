"""Engine — 模块注册表与执行引擎

供全量提取、部分执行等脚本共用的基础设施。
"""
import logging
from typing import Dict, List

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

# 需要传 config 参数的模块
CONFIG_MODULES = {"db_column_overlap", "ai_db_column_summary"}

_REGISTRY = None


def get_registry() -> Dict[str, object]:
    """延迟导入，返回 (name → callable) 注册表。"""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    from extractor.modules.db_basic import generate as db_basic
    from extractor.modules.csv_basic import generate as csv_basic
    from extractor.modules.serialized_basic import generate as serialized_basic
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
    from extractor.modules.db_fk_validate import generate as db_fk_validate
    from extractor.modules.db_column_overlap import generate as db_column_overlap
    from extractor.modules.ai_db_column_summary import generate as ai_db_column_summary

    _REGISTRY = {
        "db_basic": db_basic,
        "csv_basic": csv_basic,
        "serialized_basic": serialized_basic,
        "db_column_stats": db_column_stats,
        "db_column_sample": db_column_sample,
        "db_column_topk": db_column_topk,
        "csv_info": csv_info,
        "csv_column_stats": csv_column_stats,
        "csv_column_sample": csv_column_sample,
        "csv_column_topk": csv_column_topk,
        "json_pattern": json_pattern,
        "text_info": text_info,
        "db_table_relations": db_table_relations,
        "db_fk_validate": db_fk_validate,
        "db_column_overlap": db_column_overlap,
        "ai_db_column_summary": ai_db_column_summary,
    }

    # Agent 模块（需要 agent API key）
    try:
        from extractor.modules.agent_analyze import generate as agent_analyze
        _REGISTRY["agent_analyze"] = agent_analyze
    except ImportError:
        pass

    # Agent 模块 — 独立版（可单独调用）
    try:
        from extractor.modules.agent_join_detect import generate as agent_join_detect
        from extractor.modules.agent_disambiguate import generate as agent_disambiguate
        _REGISTRY["agent_join_detect"] = agent_join_detect
        _REGISTRY["agent_disambiguate"] = agent_disambiguate
    except ImportError:
        pass

    # 可选模块（可能被删除或未安装依赖）
    try:
        from extractor.modules.ai_db_summary import generate as ai_db_summary
        from extractor.modules.ai_db_table_summary import generate as ai_db_table_summary
        from extractor.modules.ai_json_summary import generate as ai_json_summary
        from extractor.modules.ai_text_summary import generate as ai_text_summary
        _REGISTRY["ai_db_summary"] = ai_db_summary
        _REGISTRY["ai_db_table_summary"] = ai_db_table_summary
        _REGISTRY["ai_json_summary"] = ai_json_summary
        _REGISTRY["ai_text_summary"] = ai_text_summary
    except ImportError:
        pass

    try:
        from extractor.modules.db_column_rel import generate as db_column_rel
        _REGISTRY["db_column_rel"] = db_column_rel
    except ImportError:
        pass

    return _REGISTRY


def run_pipeline(pipeline: List[str], workspace: Workspace, config=None) -> None:
    """按 pipeline 列表顺序执行模块。"""
    registry = get_registry()

    for name in pipeline:
        if name not in registry:
            logger.warning(f"Unknown module: {name}, skipping")
            continue

        func = registry[name]
        logger.info(f"  [{name}]")

        try:
            if name in CONFIG_MODULES:
                func(workspace, config=config)
            else:
                func(workspace)
        except Exception as e:
            logger.warning(f"Module {name} failed: {e}")


def init_workspace(target: str, config_path: str = None, verbose: bool = False) -> tuple:
    """初始化 Workspace 和 Config，返回 (workspace, config)。

    供各类提取脚本共用的初始化逻辑。
    """
    from extractor.modules.utils.loader import load_config
    from pathlib import Path

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s' if not verbose else '%(levelname)s: %(message)s'
    )

    target_path = Path(target).resolve()
    if not target_path.exists():
        raise ValueError(f"Target path does not exist: {target_path}")

    config = load_config(config_path)
    workspace = Workspace(project_path=str(target_path))
    return workspace, config
