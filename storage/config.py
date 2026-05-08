"""Store 配置 — 三层结构：项目信息 / 数据源 / 图存储。"""
import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Dict, List, Optional

import yaml

import logging

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """数据源配置 — 决定怎么发现和访问数据。"""
    type: str = "fs"          # fs | docker | s3 | ...
    path: str = ""


@dataclass
class GraphConfig:
    """图存储配置 — 决定图数据库存在哪、用什么引擎。"""
    type: str = "sqlite"      # sqlite | neo4j | memory
    path: str = ""            # 空 = 从 source.path 推导


@dataclass
class ProjectConfig:
    """项目配置 — 一个 project 的完整描述。"""
    name: str = ""
    description: str = ""
    source: SourceConfig = field(default_factory=SourceConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    groups: List[str] = field(default_factory=list)


@dataclass
class RoutingRule:
    pattern: str
    project: str


@dataclass
class StoreConfig:
    projects: Dict[str, ProjectConfig] = field(default_factory=dict)
    routing: List[RoutingRule] = field(default_factory=list)

    def default_project(self) -> Optional[str]:
        return next(iter(self.projects)) if self.projects else None

    def resolve_source_path(self, project: str) -> Optional[str]:
        entry = self.projects.get(project)
        if not entry:
            return None
        p = os.path.expanduser(entry.source.path)
        if entry.source.type == "fs" and not os.path.isabs(p):
            p = os.path.abspath(p)
        return p

    def resolve_graph_path(self, project: str) -> Optional[str]:
        """解析图存储路径。空则从 source.path 推导。"""
        entry = self.projects.get(project)
        if not entry:
            return None
        if entry.graph.path:
            p = os.path.expanduser(entry.graph.path)
            if not os.path.isabs(p):
                p = os.path.abspath(p)
            return p
        # 默认：{source.path}/.pontis/store.db
        src = self.resolve_source_path(project)
        if src:
            return os.path.join(src, ".pontis", "store.db")
        return None

    def route_entity(self, entity_name: str) -> Optional[str]:
        for rule in self.routing:
            if fnmatch(entity_name, rule.pattern):
                return rule.project
        return None

    def project_groups(self, project: str) -> List[str]:
        entry = self.projects.get(project)
        return entry.groups if entry else []


def _parse_project(name: str, pdata) -> ProjectConfig:
    """从 YAML 数据解析 ProjectConfig。"""
    if not isinstance(pdata, dict):
        raise ValueError(f"Project '{name}' config must be a dict with 'source' key, got {type(pdata).__name__}")

    if "source" not in pdata:
        raise ValueError(f"Project '{name}' missing required 'source' key")

    src = pdata["source"]
    source_cfg = SourceConfig(
        type=src.get("type", "fs"),
        path=src.get("path", ""),
    )
    graph = pdata.get("graph", {})
    graph_cfg = GraphConfig(
        type=graph.get("type", "sqlite"),
        path=graph.get("path", ""),
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
    merged_routing: List[RoutingRule] = []

    for src in sources:
        with open(src, "r") as f:
            data = yaml.safe_load(f) or {}
        for name, pdata in data.get("projects", {}).items():
            merged_projects[name] = _parse_project(name, pdata)
        for rdata in data.get("routing", []):
            merged_routing.append(RoutingRule(
                pattern=rdata["pattern"],
                project=rdata["project"],
            ))

    if project_path:
        pname = os.path.basename(os.path.abspath(project_path))
        if pname not in merged_projects:
            merged_projects[pname] = ProjectConfig(
                name=pname,
                source=SourceConfig(type="fs", path=project_path),
            )
            logger.info("Registered project '%s' from project_path", pname)

    return StoreConfig(projects=merged_projects, routing=merged_routing)
