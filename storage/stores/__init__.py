from storage.stores.fs import FSStore


_STORE_REGISTRY = {
    "fs": FSStore,
}


def create_store(backend: str, path: str):
    """Store 工厂函数。

    Args:
        backend: 存储后端类型，如 "fs"、未来可扩展 "s3"、"db" 等
        path: 后端特定的连接路径
    """
    cls = _STORE_REGISTRY.get(backend)
    if not cls:
        raise ValueError(f"Unknown store backend: {backend!r}")
    return cls(path)


def register_backend(name: str, store_cls):
    """注册新的存储后端。供插件扩展使用。"""
    _STORE_REGISTRY[name] = store_cls


__all__ = ["FSStore", "create_store", "register_backend"]
