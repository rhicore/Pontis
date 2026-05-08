"""GraphBackend — 图数据库持久化后端抽象层。"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

__all__ = ["GraphBackend", "create_backend"]


class GraphBackend(ABC):
    """图数据库持久化后端接口。"""

    def connect(self):
        """建立连接、创建表。"""

    def close(self):
        """关闭连接。"""

    # ==================== Nodes ====================

    @abstractmethod
    def scan_nodes(self) -> List[Tuple[str, dict]]:
        """扫描全部节点，返回 [(ent_id, props), ...]。"""

    @abstractmethod
    def read_node(self, ent_id: str) -> Optional[dict]:
        """读取单个节点属性，不存在返回 None。"""

    @abstractmethod
    def write_node(self, ent_id: str, props: dict):
        """写入或替换节点属性。"""

    @abstractmethod
    def delete_node(self, ent_id: str):
        """删除节点。"""

    # ==================== Edges ====================

    @abstractmethod
    def read_edges(self) -> List[dict]:
        """读取全部边，返回 [{"nodes": [a_id, b_id]}, ...]。"""

    @abstractmethod
    def write_edges(self, edges: List[dict]):
        """全量替换边。"""

    @abstractmethod
    def add_edge(self, a_id: str, b_id: str):
        """添加单条边（双向）。"""

    @abstractmethod
    def remove_edges_for(self, ent_id: str):
        """删除与 ent_id 相关的所有边。"""

    # ==================== Version ====================

    @abstractmethod
    def read_version(self) -> int:
        """读取当前版本号。"""

    @abstractmethod
    def bump_version(self) -> int:
        """递增版本号，返回新值。"""

    # ==================== Cross Edges ====================

    @abstractmethod
    def add_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        """添加跨项目边。"""

    @abstractmethod
    def remove_cross_edge(self, from_id: str, to_project: str, to_entity_id: str):
        """删除单条跨项目边。"""

    @abstractmethod
    def remove_cross_edges_for(self, from_id: str):
        """删除节点的所有跨项目边。"""

    @abstractmethod
    def set_cross_edge_stale(self, from_id: str, to_project: str,
                             to_entity_id: str, *, stale: bool):
        """标记跨项目边为 stale。"""

    @abstractmethod
    def read_cross_edges(self) -> Dict[str, List[dict]]:
        """加载全部跨项目边。"""


def _registry():
    """延迟加载后端注册表，避免循环导入。"""
    from storage.backends.sqlite import SQLiteBackend
    return {"sqlite": SQLiteBackend}


def create_backend(backend_type: str, path: str) -> "GraphBackend":
    """后端工厂。

    Args:
        backend_type: 后端类型，如 "sqlite"
        path: 存储路径（SQLite 为 .db 文件路径）
    """
    reg = _registry()
    cls = reg.get(backend_type)
    if not cls:
        raise ValueError(f"Unknown graph backend type: {backend_type!r}")
    return cls(path)
