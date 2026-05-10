"""Store 工厂 — 根据 ProjectConfig 创建 Store 实例。"""
import os
from dataclasses import replace

from storage.config import ProjectConfig
from storage.backends import create_backend
from storage.stores.fs import FSStore
from storage.stores.graph import GraphStore

_STORE_REGISTRY = {
    "fs": FSStore,
    "graph": GraphStore,
}


def create_store(config: ProjectConfig):
    """Store 工厂。

    根据 config.source.type 选择 Store 子类，
    根据 config.graph.type 创建持久化后端。

    Args:
        config: 项目配置（含 source + graph 配置）
    """
    # 解析 graph.path（空则从 source.path 推导）
    graph_path = config.graph.path
    if not graph_path and config.source.path:
        src = os.path.abspath(os.path.expanduser(config.source.path))
        graph_path = os.path.join(src, ".pontis", "store.db")

    # 确保目录存在
    if graph_path:
        os.makedirs(os.path.dirname(graph_path), exist_ok=True)

    # 创建后端
    backend = create_backend(config.graph.type, graph_path)

    # 创建 Store 子类
    source_type = config.source.type or "graph"
    store_cls = _STORE_REGISTRY.get(source_type)
    if not store_cls:
        raise ValueError(f"Unknown source type: {source_type!r}")

    source_cfg = replace(
        config.source,
        path=os.path.abspath(os.path.expanduser(config.source.path)) if config.source.path else "",
    )
    return store_cls(source_cfg, backend)


def register_backend(name: str, store_cls):
    """注册新的数据源类型。"""
    _STORE_REGISTRY[name] = store_cls


__all__ = ["FSStore", "create_store", "register_backend"]
