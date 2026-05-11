"""Store 工厂。

- `storage.store.Store` 是唯一主图实现
- `storage.stores.*` 下面只放 source 模块
"""

import os
from dataclasses import replace

from storage.config import ProjectConfig
from storage.backends import create_backend
from storage.stores.fs import FSModule

_MODULE_REGISTRY = {
    "fs": FSModule,
}


def create_store(config: ProjectConfig):
    """根据 project config 创建主图 Store，并按 source 类型挂模块。"""
    from storage.store import Store

    graph_path = config.graph.path
    if not graph_path and config.source.path:
        src = os.path.abspath(os.path.expanduser(config.source.path))
        graph_path = os.path.join(src, ".pontis", "store.db")

    if graph_path:
        os.makedirs(os.path.dirname(graph_path), exist_ok=True)

    backend = create_backend(config.graph.type, graph_path)
    source_cfg = replace(
        config.source,
        path=os.path.abspath(os.path.expanduser(config.source.path)) if config.source.path else "",
    )

    store = Store(source_cfg, backend)
    mod_cls = _MODULE_REGISTRY.get(source_cfg.type or "")
    if mod_cls:
        store.add_module(mod_cls(store))
    return store


def register_module(name: str, module_cls):
    _MODULE_REGISTRY[name] = module_cls


__all__ = ["FSModule", "create_store", "register_module"]
