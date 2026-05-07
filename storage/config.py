"""Store 配置 — project 映射、路由规则、加载逻辑。"""
import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Dict, List, Optional

import yaml

import logging

logger = logging.getLogger(__name__)


@dataclass
class ProjectEntry:
    path: str
    backend: str = "fs"
    groups: List[str] = field(default_factory=list)


@dataclass
class RoutingRule:
    pattern: str
    project: str


@dataclass
class StoreConfig:
    projects: Dict[str, ProjectEntry] = field(default_factory=dict)
    routing: List[RoutingRule] = field(default_factory=list)

    def default_project(self) -> Optional[str]:
        return next(iter(self.projects)) if self.projects else None

    def resolve_path(self, project: str) -> Optional[str]:
        entry = self.projects.get(project)
        if entry:
            p = os.path.expanduser(entry.path)
            # fs backend 做 abspath，其他 backend（s3/db 等）保持原样
            if entry.backend == "fs" and not os.path.isabs(p):
                p = os.path.abspath(p)
            return p
        return None

    def resolve_backend(self, project: str) -> str:
        entry = self.projects.get(project)
        return entry.backend if entry else "fs"

    def route_entity(self, entity_name: str) -> Optional[str]:
        for rule in self.routing:
            if fnmatch(entity_name, rule.pattern):
                return rule.project
        return None

    def project_groups(self, project: str) -> List[str]:
        entry = self.projects.get(project)
        return entry.groups if entry else []


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

    merged_projects = {}
    merged_routing = []

    for src in sources:
        with open(src, "r") as f:
            data = yaml.safe_load(f) or {}
        for name, pdata in data.get("projects", {}).items():
            if isinstance(pdata, dict):
                merged_projects[name] = ProjectEntry(
                    path=pdata["path"],
                    backend=pdata.get("backend", "fs"),
                    groups=pdata.get("groups", []),
                )
            else:
                merged_projects[name] = ProjectEntry(path=pdata)
        for rdata in data.get("routing", []):
            merged_routing.append(RoutingRule(
                pattern=rdata["pattern"],
                project=rdata["project"],
            ))

    if project_path:
        # project_path 始终注册（除非配置中已存在同名项目）
        pname = os.path.basename(os.path.abspath(project_path))
        if pname not in merged_projects:
            merged_projects[pname] = ProjectEntry(path=project_path)
            logger.info("Registered project '%s' from project_path", pname)

    return StoreConfig(projects=merged_projects, routing=merged_routing)
