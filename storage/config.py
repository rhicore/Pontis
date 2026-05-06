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
    default: bool = False
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
        for name, entry in self.projects.items():
            if entry.default:
                return name
        return next(iter(self.projects)) if self.projects else None

    def resolve_path(self, project: str) -> Optional[str]:
        entry = self.projects.get(project)
        if entry:
            return os.path.abspath(os.path.expanduser(entry.path))
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
    """加载配置，优先级：config_path > project_path/pontis.yml > fallback。"""
    sources = []
    if config_path:
        sources.append(config_path)
    if project_path:
        for filename in ("pontis.yml", "pontis.yaml"):
            p = os.path.join(project_path, filename)
            if os.path.exists(p):
                sources.append(p)
                break
    global_cfg = os.path.expanduser("~/.pontis/config.yml")
    if os.path.exists(global_cfg):
        sources.append(global_cfg)

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
                    default=pdata.get("default", False),
                    groups=pdata.get("groups", []),
                )
            else:
                merged_projects[name] = ProjectEntry(path=pdata)
        for rdata in data.get("routing", []):
            merged_routing.append(RoutingRule(
                pattern=rdata["pattern"],
                project=rdata["project"],
            ))

    if not merged_projects and project_path:
        pname = os.path.basename(os.path.abspath(project_path))
        merged_projects[pname] = ProjectEntry(path=project_path, default=True)
        logger.info("No config found; using project name '%s' (fallback)", pname)

    return StoreConfig(projects=merged_projects, routing=merged_routing)
