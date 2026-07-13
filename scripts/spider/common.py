"""Shared helpers for Spider2-Snow Pontis scripts."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
SPIDER2_ROOT = TEXT2SQL_ROOT / "data" / "Spider2"
SPIDER2_SNOW_ROOT = SPIDER2_ROOT / "spider2-snow"
SPIDER2_SNOW_CASES = SPIDER2_SNOW_ROOT / "spider2-snow.jsonl"
SPIDER2_SNOW_RESOURCE = SPIDER2_SNOW_ROOT / "resource"
SPIDER2_SNOW_DATABASES = SPIDER2_SNOW_RESOURCE / "databases"
SPIDER2_SNOW_DOCUMENTS = SPIDER2_SNOW_RESOURCE / "documents"
SPIDER2_SNOW_EVAL_SUITE = SPIDER2_SNOW_ROOT / "evaluation_suite"
SPIDER2_SNOW_GOLD_SQL_DIR = SPIDER2_SNOW_EVAL_SUITE / "gold" / "sql"
SPIDER2_SNOW_CREDENTIAL = SPIDER2_SNOW_EVAL_SUITE / "snowflake_credential.json"

PONTIS_WORKSPACE_ROOT = TEXT2SQL_ROOT / "workspace" / "baselines" / "pontis"
SPIDER_WORKSPACE_ROOT = PONTIS_WORKSPACE_ROOT / "spider2_snow"
SPIDER_NEO4J_BOLT_BASE = int(os.environ.get("PONTIS_SPIDER_NEO4J_BOLT_BASE", "7900"))
SPIDER_NEO4J_URI = os.environ.get("PONTIS_SPIDER_NEO4J_URI", f"bolt://127.0.0.1:{SPIDER_NEO4J_BOLT_BASE}")
SPIDER_NEO4J_RUNTIME_PROJECT = os.environ.get("PONTIS_SPIDER_NEO4J_RUNTIME_PROJECT", "spider2_snow")
SPIDER_NEO4J_HEAP_INITIAL = os.environ.get("PONTIS_SPIDER_NEO4J_HEAP_INITIAL", "512m")
SPIDER_NEO4J_HEAP_MAX = os.environ.get("PONTIS_SPIDER_NEO4J_HEAP_MAX", "4g")
SPIDER_NEO4J_PAGECACHE = os.environ.get("PONTIS_SPIDER_NEO4J_PAGECACHE", "1g")
SPIDER_PONTIS_CONFIG_MARKER_BEGIN = "# BEGIN Spider2-Snow projects"
SPIDER_PONTIS_CONFIG_MARKER_END = "# END Spider2-Snow projects"
LEGACY_SPIDER_CONFIG_MARKER_BEGIN = "  # BEGIN Spider2 projects"
LEGACY_SPIDER_CONFIG_MARKER_END = "  # END Spider2 projects"

_RUN_ID = (
    os.environ.get("PONTIS_SPIDER_RUN_ID")
    or os.environ.get("TEXT2SQL_RUN_ID")
    or os.environ.get("BASELINE_RUN_ID")
    or datetime.now().strftime("%Y%m%d_%H%M%S")
)
_RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
_TIMESTAMP_ONLY_RE = re.compile(r"^\d{8}_\d{6}$")
_TIMESTAMP_PREFIX_RE = re.compile(r"^\d{8}_\d{6}(?:_|$)")


@dataclass(frozen=True)
class SpiderSnowCase:
    instance_id: str
    instruction: str
    db_id: str
    external_knowledge: str | None = None

    @classmethod
    def from_row(cls, row: dict) -> "SpiderSnowCase":
        return cls(
            instance_id=str(row["instance_id"]),
            instruction=str(row["instruction"]),
            db_id=str(row["db_id"]),
            external_knowledge=row.get("external_knowledge"),
        )

    def to_row(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "instruction": self.instruction,
            "db_id": self.db_id,
            "external_knowledge": self.external_knowledge,
        }


def get_run_id() -> str:
    return _RUN_ID


def set_run_id(run_id: str) -> None:
    global _RUN_ID
    _RUN_ID = run_id
    os.environ.setdefault("TEXT2SQL_RUN_ID", run_id)


def get_run_name(run_id: str | None = None) -> str:
    raw_run_id = (run_id or get_run_id()).strip()
    split = "spider2_snow"
    if _TIMESTAMP_ONLY_RE.match(raw_run_id):
        return f"{raw_run_id}_{split}"
    if _TIMESTAMP_PREFIX_RE.match(raw_run_id):
        return raw_run_id
    if raw_run_id.startswith(f"{split}_"):
        return f"{_RUN_TIMESTAMP}_{raw_run_id}"
    return f"{_RUN_TIMESTAMP}_{split}_{raw_run_id}"


def get_projects_root() -> Path:
    return SPIDER_WORKSPACE_ROOT / "projects"


def get_project_dir(db_id: str) -> Path:
    return get_projects_root() / db_id


def get_preprocess_dir(db_id: str) -> Path:
    return PONTIS_WORKSPACE_ROOT / "preprocess_logs" / get_run_name() / "spider2_snow" / db_id


def get_runtime_dir(db_id: str) -> Path:
    return PONTIS_WORKSPACE_ROOT / "runtime_logs" / get_run_name() / "spider2_snow" / db_id


def get_results_dir() -> Path:
    return PONTIS_WORKSPACE_ROOT / "results" / get_run_name() / "spider2_snow"


def parse_csv_arg(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or None


def load_spider2_snow_cases(
    *,
    db: str | None = None,
    instances: Iterable[str] | None = None,
    limit: int | None = None,
    dev_only: bool = False,
) -> list[SpiderSnowCase]:
    if not SPIDER2_SNOW_CASES.exists():
        raise FileNotFoundError(f"Spider2-Snow case file not found: {SPIDER2_SNOW_CASES}")

    db_filter = set(parse_csv_arg(db) or [])
    instance_filter = {str(item) for item in instances or []}
    dev_instance_filter = set(iter_spider2_snow_gold_sql_ids()) if dev_only else None
    cases: list[SpiderSnowCase] = []
    for line in SPIDER2_SNOW_CASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = SpiderSnowCase.from_row(json.loads(line))
        if dev_instance_filter is not None and case.instance_id not in dev_instance_filter:
            continue
        if db_filter and case.db_id not in db_filter:
            continue
        if instance_filter and case.instance_id not in instance_filter:
            continue
        cases.append(case)
        if limit is not None and len(cases) >= limit:
            break
    return cases


def iter_spider2_snow_gold_sql_ids() -> list[str]:
    """Return Spider2-Snow cases that have local gold SQL for correctness debugging."""

    if not SPIDER2_SNOW_GOLD_SQL_DIR.exists():
        raise FileNotFoundError(f"Spider2-Snow gold SQL directory not found: {SPIDER2_SNOW_GOLD_SQL_DIR}")
    return sorted(path.stem for path in SPIDER2_SNOW_GOLD_SQL_DIR.glob("*.sql"))


def group_cases_by_db(cases: Iterable[SpiderSnowCase]) -> dict[str, list[SpiderSnowCase]]:
    grouped: dict[str, list[SpiderSnowCase]] = {}
    for case in cases:
        grouped.setdefault(case.db_id, []).append(case)
    return dict(sorted(grouped.items()))


def prepare_spider2_snow_project(
    db_id: str,
    cases: Iterable[SpiderSnowCase],
    *,
    force: bool = False,
) -> dict:
    """Create a local filesystem project for one Spider2-Snow database."""

    project_dir = get_project_dir(db_id)
    db_src = SPIDER2_SNOW_DATABASES / db_id
    if not db_src.exists():
        raise FileNotFoundError(f"Spider2-Snow database resource not found: {db_src}")

    if force and project_dir.exists():
        shutil.rmtree(project_dir)

    project_dir.mkdir(parents=True, exist_ok=True)
    # Project routing lives in Pontis/pontis.yml so Neo4j management and agent
    # creation behave like BIRD projects.
    (project_dir / "pontis.yml").unlink(missing_ok=True)
    database_dst = project_dir / "database"
    if force and database_dst.exists():
        shutil.rmtree(database_dst)
    if not database_dst.exists():
        shutil.copytree(db_src, database_dst)

    manifest_dir = project_dir / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    case_list = list(cases)
    manifest_path = manifest_dir / "spider2_snow_cases.jsonl"
    manifest_path.write_text(
        "".join(json.dumps(case.to_row(), ensure_ascii=False) + "\n" for case in case_list),
        encoding="utf-8",
    )

    docs_dir = project_dir / "documents"
    docs_dir.mkdir(exist_ok=True)
    missing_docs: list[str] = []
    copied_docs: list[str] = []
    for doc_name in sorted({case.external_knowledge for case in case_list if case.external_knowledge}):
        source = SPIDER2_SNOW_DOCUMENTS / str(doc_name)
        target = docs_dir / str(doc_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copy2(source, target)
            copied_docs.append(str(doc_name))
        else:
            missing_docs.append(str(doc_name))

    readme = _build_project_readme(db_id, case_list, copied_docs, missing_docs)
    (project_dir / "README.md").write_text(readme, encoding="utf-8")

    return {
        "db_id": db_id,
        "project_dir": str(project_dir),
        "graph_uri": _graph_uri_for_db(db_id),
        "source_database_dir": str(db_src),
        "cases": len(case_list),
        "documents": copied_docs,
        "missing_documents": missing_docs,
    }


def _graph_uri_for_db(db_id: str) -> str:
    return SPIDER_NEO4J_URI


def _spider2_snow_graph_config_path() -> Path:
    return PONTIS_WORKSPACE_ROOT / "neo4j" / "spider2_snow_shared.yml"


def _write_spider2_snow_graph_config() -> Path:
    path = _spider2_snow_graph_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "projects:",
                f"  {SPIDER_NEO4J_RUNTIME_PROJECT}:",
                "    source:",
                "      type: fs",
                f"      path: {SPIDER_WORKSPACE_ROOT}",
                "    graph:",
                f"      uri: {SPIDER_NEO4J_URI}",
                "      database: neo4j",
                "      user: neo4j",
                "      password_env: NEO4J_PASSWORD",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _spider2_snow_graph_port_open(timeout: float = 0.5) -> bool:
    match = re.match(r"^(?:bolt|neo4j)://([^:/]+):(\d+)$", SPIDER_NEO4J_URI)
    if not match:
        return False
    host, raw_port = match.groups()
    if host == "localhost":
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, int(raw_port)), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_spider2_snow_neo4j() -> Path:
    """Start the shared Spider2-Snow Neo4j process if its Bolt port is closed."""
    config_path = _write_spider2_snow_graph_config()
    if _spider2_snow_graph_port_open():
        return config_path

    cmd = [
        sys.executable,
        "-m",
        "scripts.neo4j_instances",
        "start",
        SPIDER_NEO4J_RUNTIME_PROJECT,
        "--config",
        str(config_path),
        "--base-dir",
        str(PONTIS_WORKSPACE_ROOT / "neo4j" / "projects"),
        "--heap-initial",
        SPIDER_NEO4J_HEAP_INITIAL,
        "--heap-max",
        SPIDER_NEO4J_HEAP_MAX,
        "--pagecache",
        SPIDER_NEO4J_PAGECACHE,
        "--start-grace",
        "4",
    ]
    subprocess.run(cmd, cwd=str(PONTIS_ROOT), check=True)
    return config_path


def spider2_snow_project_config_block(db_ids: Iterable[str] | None = None) -> str:
    ids = sorted(set(db_ids or iter_spider2_snow_db_ids()))
    lines = [
        SPIDER_PONTIS_CONFIG_MARKER_BEGIN,
        "  # Generated Spider2-Snow filesystem projects.",
        f"  # All Spider2-Snow projects share {SPIDER_NEO4J_URI} and are isolated by the reserved project property.",
        "  # Run scripts/spider/extract.py first to prepare each source path.",
    ]
    for db_id in ids:
        lines.extend(
            [
                f"  {db_id}:",
                "    source:",
                "      type: spider2_snow",
                f"      path: ../workspace/baselines/pontis/spider2_snow/projects/{db_id}",
                f"      database: {db_id}",
                "      credential_path: ../data/Spider2/spider2-snow/evaluation_suite/snowflake_credential.json",
                "    graph:",
                "      <<: *neo4j_graph",
                f"      uri: {_graph_uri_for_db(db_id)}",
            ]
        )
    lines.append(SPIDER_PONTIS_CONFIG_MARKER_END)
    return "\n".join(lines) + "\n"


def iter_spider2_snow_db_ids() -> list[str]:
    if not SPIDER2_SNOW_CASES.exists():
        return []
    db_ids = set()
    for line in SPIDER2_SNOW_CASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        db_ids.add(str(json.loads(line)["db_id"]))
    return sorted(db_ids)


def sync_spider2_snow_pontis_config(config_path: Path | None = None) -> Path:
    """Insert or replace the generated Spider2-Snow block in Pontis/pontis.yml."""

    path = config_path or (PONTIS_ROOT / "pontis.yml")
    text = path.read_text(encoding="utf-8")
    block = spider2_snow_project_config_block()
    legacy_pattern = re.compile(
        rf"\n?{re.escape(LEGACY_SPIDER_CONFIG_MARKER_BEGIN)}\n.*?"
        rf"{re.escape(LEGACY_SPIDER_CONFIG_MARKER_END)}\n?",
        re.DOTALL,
    )
    text = legacy_pattern.sub("\n", text)
    pattern = re.compile(
        rf"\n?{re.escape(SPIDER_PONTIS_CONFIG_MARKER_BEGIN)}\n.*?{re.escape(SPIDER_PONTIS_CONFIG_MARKER_END)}\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        updated = pattern.sub("\n" + block, text).rstrip() + "\n"
    else:
        updated = text.rstrip() + "\n" + block
    path.write_text(updated, encoding="utf-8")
    _remove_legacy_project_configs()
    return path


def _remove_legacy_project_configs() -> None:
    root = get_projects_root()
    if not root.exists():
        return
    for pattern in ("*/pontis.yml", "*/pontis.yaml"):
        for path in root.glob(pattern):
            path.unlink(missing_ok=True)


def _build_project_readme(
    db_id: str,
    cases: list[SpiderSnowCase],
    copied_docs: list[str],
    missing_docs: list[str],
) -> str:
    lines = [
        f"# Spider2-Snow Project: {db_id}",
        "",
        "This Pontis project is a local filesystem snapshot for Spider2-Snow.",
        "",
        "## Layout",
        "",
        "- `database/`: Snowflake DDL CSV files and table JSON metadata copied from Spider2-Snow resources.",
        "- `documents/`: external knowledge files referenced by the selected cases.",
        "- `manifest/spider2_snow_cases.jsonl`: selected Spider2-Snow cases for this project.",
        "",
        "## SQL Target",
        "",
        "Write Snowflake SQL. The Pontis source can use the configured Snowflake credential for live read-only checks, and local files provide benchmark metadata, samples, and documents.",
        "",
        "## Selected Cases",
        "",
    ]
    if cases:
        for case in cases[:100]:
            doc = case.external_knowledge or "(none)"
            lines.append(f"- `{case.instance_id}`: {case.instruction} External knowledge: `{doc}`")
        if len(cases) > 100:
            lines.append(f"- ... {len(cases) - 100} more cases")
    else:
        lines.append("- No case selected.")
    lines.extend(["", "## External Knowledge", ""])
    if copied_docs:
        for doc in copied_docs:
            lines.append(f"- `{doc}`")
    else:
        lines.append("- No external knowledge file copied.")
    if missing_docs:
        lines.extend(["", "Missing external knowledge files:"])
        for doc in missing_docs:
            lines.append(f"- `{doc}`")
    return "\n".join(lines) + "\n"
