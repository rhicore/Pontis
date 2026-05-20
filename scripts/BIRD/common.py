"""BIRD 脚本共用路径与枚举逻辑。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEXT2SQL_ROOT = PROJECT_ROOT.parent
PONTIS_WORKSPACE_ROOT = TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis"


def get_data_dir(train: bool) -> Path:
    split = "bird_train" if train else "bird_dev"
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
    split = "bird_train" if train else "bird_dev"
    return PONTIS_WORKSPACE_ROOT / "preprocess_logs" / split / db_id


def get_runtime_dir(db_id: str, train: bool) -> Path:
    split = "bird_train" if train else "bird_dev"
    return PONTIS_WORKSPACE_ROOT / "runtime_logs" / split / db_id


def get_benchmark_dir(db_id: str, train: bool) -> Path:
    return get_runtime_dir(db_id, train) / "benchmark"


def get_progress_path(train: bool) -> Path:
    split = "bird_train" if train else "bird_dev"
    return PONTIS_WORKSPACE_ROOT / "runtime_logs" / split / "progress.log"


def get_results_dir(train: bool) -> Path:
    split = "bird_train" if train else "bird_dev"
    return PONTIS_WORKSPACE_ROOT / "results" / split


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
