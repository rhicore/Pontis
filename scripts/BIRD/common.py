"""BIRD 脚本共用路径与枚举逻辑。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_data_dir(train: bool) -> Path:
    return PROJECT_ROOT / "example_data" / ("bird_train" if train else "bird_dev")


def get_db_base(train: bool) -> Path:
    data_dir = get_data_dir(train)
    return data_dir / ("train_databases" if train else "dev_databases")


def get_db_dir(db_id: str, train: bool) -> Path:
    return get_db_base(train) / db_id


def get_benchmark_dir(db_id: str, train: bool) -> Path:
    return get_db_dir(db_id, train) / ".pontis" / "benchmark"


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
        bench_dir = db_dir / ".pontis" / "benchmark"
        if bench_dir.exists() and any(bench_dir.glob("*.brief.log")):
            db_ids.append(db_dir.name)
    return db_ids
