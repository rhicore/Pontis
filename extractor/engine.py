"""Engine — 模块注册表与执行引擎

供全量提取、部分执行等脚本共用的基础设施。
"""
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

# 需要传 config 参数的模块
CONFIG_MODULES = {"db_column_overlap", "ai_db_column_summary", "semantic_embedding"}

_REGISTRY = None

_DEFAULT_FILE_FMT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
_DEFAULT_LOG_DATE = "%H:%M:%S"


@dataclass
class RunOptions:
    """模块执行选项。"""

    continue_on_error: bool = True
    collect_timing: bool = False
    module_kwargs: Optional[Dict[str, Dict[str, Any]]] = None


@contextmanager
def file_log_handler(
    log_path: str,
    *,
    level: int = logging.DEBUG,
    fmt: str = _DEFAULT_FILE_FMT,
    datefmt: str = _DEFAULT_LOG_DATE,
) -> Iterator[logging.FileHandler]:
    """临时挂载文件日志 handler。"""
    owner_thread_id = threading.get_ident()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    fh.addFilter(lambda record: record.thread == owner_thread_id)
    root = logging.getLogger()
    root.addHandler(fh)
    try:
        yield fh
    finally:
        root.removeHandler(fh)
        fh.close()


def get_registry() -> Dict[str, object]:
    """延迟导入，返回 (name → callable) 注册表。"""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    from extractor.modules.db_column_stats_approx import generate as db_column_stats_approx
    from extractor.modules.csv_column_stats import generate as csv_column_stats
    from extractor.modules.csv_column_sample import generate as csv_column_sample
    from extractor.modules.csv_column_topk import generate as csv_column_topk
    from extractor.modules.json_pattern import generate as json_pattern
    from extractor.modules.db_fk_validate import generate as db_fk_validate
    from extractor.modules.db_column_overlap import generate as db_column_overlap
    from extractor.modules.ai_db_column_summary import generate as ai_db_column_summary
    from extractor.modules.semantic_embedding import generate as semantic_embedding
    from extractor.modules.official_description_extract import generate as official_description_extract

    _REGISTRY = {
        "db_column_stats_approx": db_column_stats_approx,
        "csv_column_stats": csv_column_stats,
        "csv_column_sample": csv_column_sample,
        "csv_column_topk": csv_column_topk,
        "json_pattern": json_pattern,
        "db_fk_validate": db_fk_validate,
        "db_column_overlap": db_column_overlap,
        "ai_db_column_summary": ai_db_column_summary,
        "official_description_extract": official_description_extract,
        "semantic_embedding": semantic_embedding,
    }

    # Explorer 模块 — 独立版（可单独调用）
    try:
        from explorer.schema_prepare import generate as agent_schema_prepare
        from explorer.relation_disambiguation_review import generate as agent_relation_disambiguation_review
        from explorer.description_audit import generate as agent_description_audit
        from explorer.disambiguate import generate as agent_disambiguate
        from explorer.bird_profile import generate as agent_bird_profile
        from explorer.readme import generate as agent_readme
        _REGISTRY["agent_schema_prepare"] = agent_schema_prepare
        _REGISTRY["agent_relation_disambiguation_review"] = agent_relation_disambiguation_review
        _REGISTRY["agent_description_audit"] = agent_description_audit
        _REGISTRY["agent_disambiguate"] = agent_disambiguate
        _REGISTRY["agent_bird_profile"] = agent_bird_profile
        _REGISTRY["agent_readme"] = agent_readme
    except ImportError:
        pass

    # 可选模块（可能被删除或未安装依赖）
    try:
        from extractor.modules.ai_db_summary import generate as ai_db_summary
        from extractor.modules.ai_db_table_summary import generate as ai_db_table_summary
        from extractor.modules.ai_json_summary import generate as ai_json_summary
        _REGISTRY["ai_db_summary"] = ai_db_summary
        _REGISTRY["ai_db_table_summary"] = ai_db_table_summary
        _REGISTRY["ai_json_summary"] = ai_json_summary
    except ImportError:
        pass

    return _REGISTRY


def run_modules(
    module_names: List[str],
    workspace: Workspace,
    config=None,
    *,
    options: Optional[RunOptions] = None,
) -> Dict[str, float]:
    """按顺序执行模块，返回每个成功模块的耗时。"""
    registry = get_registry()
    options = options or RunOptions()
    timings: Dict[str, float] = {}

    for name in module_names:
        if name not in registry:
            logger.warning(f"Unknown module: {name}, skipping")
            continue

        func = registry[name]
        logger.info(f"  [{name}]")

        try:
            t0 = time.time()
            kwargs = (options.module_kwargs or {}).get(name, {})
            if name in CONFIG_MODULES:
                module_result = func(workspace, config=config, **kwargs)
            else:
                module_result = func(workspace, **kwargs)
            if (
                config is not None
                and isinstance(module_result, dict)
                and hasattr(config, "add_preprocess_token_metrics")
            ):
                config.add_preprocess_token_metrics(module_result)
            if options.collect_timing:
                timings[name] = time.time() - t0
        except Exception as e:
            logger.warning(f"Module {name} failed: {e}")
            if not options.continue_on_error:
                raise

    return timings


def run_pipeline(pipeline: List[str], workspace: Workspace, config=None) -> None:
    """按 pipeline 列表顺序执行模块。"""
    run_modules(pipeline, workspace, config=config)


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
