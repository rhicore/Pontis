"""Store 工厂。

- `storage.store.Store` 是唯一主图实现
- `storage.stores.*` 下面只放 source 模块
"""

import os
from dataclasses import replace

from storage.config import ProjectConfig
from storage.neo4j import Neo4jGraph
from storage.stores.base import ModuleContext
from storage.stores.utils.fs_adapter import LocalSourceAdapter
from storage.stores.fs import FSModule
from storage.stores.text import TextModule
from storage.stores.csv_schema import CSVSchemaModule
from storage.stores.db_schema import SQLiteSchemaModule

_MODULE_REGISTRY = {
    "fs": [FSModule, TextModule, CSVSchemaModule, SQLiteSchemaModule],
}


def create_store(config: ProjectConfig):
    """根据 project config 创建主图 Store，并按 source 类型挂模块。"""
    from storage.store import Store

    graph = Neo4jGraph(
        uri=config.graph.uri,
        database=config.graph.database,
        user=config.graph.user,
        password=config.graph.password,
        password_env=config.graph.password_env,
    )
    source_cfg = replace(
        config.source,
        path=os.path.abspath(os.path.expanduser(config.source.path)) if config.source.path else "",
    )

    store = Store(source_cfg, graph)
    mod_entry = _MODULE_REGISTRY.get(source_cfg.type or "")
    if mod_entry:
        ctx = ModuleContext(
            project_name=config.name,
            project_config=config,
            source_config=source_cfg,
            graph_config=config.graph,
            source=LocalSourceAdapter(source_cfg.path),
        )
        mod_classes = mod_entry if isinstance(mod_entry, list) else [mod_entry]
        for mod_cls in mod_classes:
            store.add_module(mod_cls(ctx))
    return store


def register_module(name: str, module_cls):
    _MODULE_REGISTRY[name] = module_cls


__all__ = ["FSModule", "TextModule", "CSVSchemaModule", "SQLiteSchemaModule", "create_store", "register_module"]
