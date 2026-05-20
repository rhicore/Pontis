"""Store 配置 — 三层结构：项目信息 / 数据源 / 图存储。"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

import logging

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """数据源配置 — 决定怎么发现和访问数据。"""
    type: str = ""            # fs | docker | s3 | ...
    path: str = ""


@dataclass
class GraphConfig:
    """Neo4j 连接配置。"""
    uri: str = ""             # Neo4j bolt URI
    database: str = ""        # Neo4j database name
    user: str = ""            # Neo4j username
    password: str = ""        # Neo4j password, prefer password_env
    password_env: str = ""    # Env var containing Neo4j password


@dataclass
class ProjectConfig:
    """项目配置 — 一个 project 的完整描述。"""
    name: str = ""
    description: str = ""
    source: SourceConfig = field(default_factory=SourceConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    groups: List[str] = field(default_factory=list)


@dataclass
class StoreConfig:
    projects: Dict[str, ProjectConfig] = field(default_factory=dict)

    def resolve_source_path(self, project: str) -> Optional[str]:
        entry = self.projects.get(project)
        if not entry:
            return None
        p = os.path.expanduser(entry.source.path)
        if entry.source.type == "fs" and not os.path.isabs(p):
            p = os.path.abspath(p)
        return p

    def resolve_graph_uri(self, project: str) -> Optional[str]:
        entry = self.projects.get(project)
        if not entry:
            return None
        return entry.graph.uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")

    def project_groups(self, project: str) -> List[str]:
        entry = self.projects.get(project)
        return entry.groups if entry else []


def _parse_project(name: str, pdata) -> ProjectConfig:
    """从 YAML 数据解析 ProjectConfig。"""
    if not isinstance(pdata, dict):
        raise ValueError(f"Project '{name}' config must be a dict, got {type(pdata).__name__}")

    src = pdata.get("source", {})
    source_cfg = SourceConfig(
        type=src.get("type", ""),
        path=src.get("path", ""),
    )
    graph = pdata.get("graph", {})
    graph_cfg = GraphConfig(
        uri=graph.get("uri", ""),
        database=graph.get("database", ""),
        user=graph.get("user", ""),
        password=graph.get("password", ""),
        password_env=graph.get("password_env", ""),
    )
    return ProjectConfig(
        name=name,
        description=pdata.get("description", ""),
        source=source_cfg,
        graph=graph_cfg,
        groups=pdata.get("groups", []),
    )


def load_config(config_path: str = None, project_path: str = None) -> StoreConfig:
    """加载配置，优先级：config_path > project_path/pontis.yml > 全局 pontis.yml。

    全局 pontis.yml 位于项目根目录（Pontis/pontis.yml），定义可用项目和默认开启项。
    """
    sources = []
    if config_path:
        sources.append(config_path)
    env_config = os.environ.get("PONTIS_CONFIG_PATH") or os.environ.get("PONTIS_CONFIG")
    if env_config:
        sources.append(env_config)
    if project_path:
        for filename in ("pontis.yml", "pontis.yaml"):
            p = os.path.join(project_path, filename)
            if os.path.exists(p):
                sources.append(p)
                break
    # 全局默认配置（项目根目录 pontis.yml）
    builtin_cfg = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pontis.yml")
    if os.path.exists(builtin_cfg):
        sources.append(builtin_cfg)

    merged_projects: Dict[str, ProjectConfig] = {}
    for src in sources:
        with open(src, "r") as f:
            data = yaml.safe_load(f) or {}
        for name, pdata in data.get("projects", {}).items():
            if name not in merged_projects:
                merged_projects[name] = _parse_project(name, pdata)

    if project_path:
        pname = os.path.basename(os.path.abspath(project_path))
        if pname not in merged_projects:
            merged_projects[pname] = ProjectConfig(
                name=pname,
                source=SourceConfig(type="fs", path=project_path),
            )
            logger.info("Registered project '%s' from project_path", pname)

    return StoreConfig(projects=merged_projects)
