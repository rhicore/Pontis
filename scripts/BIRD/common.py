"""BIRD 脚本共用路径与枚举逻辑。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEXT2SQL_ROOT = PROJECT_ROOT.parent
PONTIS_WORKSPACE_ROOT = TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis"
_RUN_ID = (
    os.environ.get("PONTIS_BIRD_RUN_ID")
    or os.environ.get("TEXT2SQL_RUN_ID")
    or os.environ.get("BASELINE_RUN_ID")
    or datetime.now().strftime("%Y%m%d_%H%M%S")
)


def get_run_id() -> str:
    return _RUN_ID


def set_run_id(run_id: str) -> None:
    global _RUN_ID
    _RUN_ID = run_id
    os.environ.setdefault("TEXT2SQL_RUN_ID", run_id)


def get_split_name(train: bool) -> str:
    return "bird_train" if train else "bird_dev"


def get_run_name(train: bool, run_id: str | None = None) -> str:
    return f"{get_split_name(train)}_{run_id or get_run_id()}"


def get_data_dir(train: bool) -> Path:
    split = get_split_name(train)
    workspace_data = TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis" / "data" / split
    if workspace_data.exists():
        return workspace_data
    local_data = PROJECT_ROOT / "example_data" / split
    if local_data.exists():
        return local_data
    return TEXT2SQL_ROOT / "example_data" / split


def get_db_base(train: bool) -> Path:
    data_dir = get_data_dir(train)
    return data_dir / ("train_databases" if train else "dev_databases")


def get_db_dir(db_id: str, train: bool) -> Path:
    return get_db_base(train) / db_id


def get_preprocess_dir(db_id: str, train: bool) -> Path:
    return PONTIS_WORKSPACE_ROOT / "preprocess_logs" / get_run_name(train) / db_id


def get_runtime_dir(db_id: str, train: bool) -> Path:
    return PONTIS_WORKSPACE_ROOT / "runtime_logs" / get_run_name(train) / db_id


def get_benchmark_dir(db_id: str, train: bool) -> Path:
    return get_runtime_dir(db_id, train) / "benchmark"


def get_progress_path(train: bool) -> Path:
    return PONTIS_WORKSPACE_ROOT / "runtime_logs" / get_run_name(train) / "progress.log"


def get_results_dir(train: bool) -> Path:
    return PONTIS_WORKSPACE_ROOT / "results" / get_run_name(train)


def iter_db_dirs(train: bool):
    db_base = get_db_base(train)
    if not db_base.exists():
        return []
    return sorted(p for p in db_base.iterdir() if p.is_dir())


def list_db_ids_with_benchmark_logs(train: bool, selected_db: str | None = None) -> list[str]:
    db_ids = []
    for db_dir in iter_db_dirs(train):
        if selected_db and db_dir.name != selected_db:
            continue
        bench_dir = get_benchmark_dir(db_dir.name, train)
        if bench_dir.exists() and any(bench_dir.glob("q*.log")):
            db_ids.append(db_dir.name)
    return db_ids
