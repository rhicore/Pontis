"""Store module protocol.

这个文件是给 source/project 模块作者看的最小协议说明。

设计目标：
- `storage.store.Store` 负责通用图逻辑
- `storage.stores.*` 下面的模块只负责某一种 project/source 语义
- 模块通过下面这组钩子，把“虚子图 / src / 匹配规则 / 虚属性”接入主图
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MatchResult:
    """中心层用的匹配结果。

    说明：
    - `matches` 放的是主图里命中的实体内部 id
    - `mergeable=False` 表示不要自动复用
    """
    matches: list[str]
    mergeable: bool = False

    @property
    def count(self) -> int:
        return len(self.matches)


@dataclass
class MatchQuery:
    """模块返回给中心层的声明式匹配查询。

    约定：
    - `query` 必须是可直接执行的 cypher
    - `params` 是执行参数
    - `var` 是结果里代表“候选主图实体”的变量名
    """
    query: str
    params: dict
    var: str = "n"


class StoreModule:
    """模块协议。

    一个模块通常对应一种 project/source 语义，例如 `fs`。

    模块作者通常只需要实现这些能力中的一部分：
    - 虚实体发现：`discover_virtual`
    - 虚子图导出：`iter_virtual_nodes` / `iter_virtual_edges`
    - 虚元数据：`get_virtual_meta`
    - 虚邻接：`get_virtual_neighbors`
    - 原生访问端口：`bind_src`
    - 主图复用规则：`match_query`
    - fallback 元数据：`meta_fallback`

    不是每个模块都要实现全部方法；不需要的能力返回空即可。
    """
    name = "module"
    prop_registry = {}
    dir_props = {}
    common_file_props = {}

    def iter_virtual_nodes(self) -> list[dict]:
        """返回模块提供的虚节点列表。

        返回的每个节点应尽量包含：
        - `name`
        - `path`（如果有稳定路径）
        - `labels`
        """
        return []

    def iter_virtual_edges(self, nodes: list[dict]) -> list[tuple[str, str]]:
        """返回虚节点之间的边。

        这里返回的是逻辑 ref/path 对，而不是持久化 ent_id。
        中心层会在物化或 merged view 中再做解析。
        """
        return []

    def discover_virtual(self, pattern: str, label: str | None = None) -> list:
        """按模式发现虚实体。

        主要给搜索/调试/兼容路径使用。
        """
        return []

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        """返回某个虚实体的元数据。

        这里返回的内容会参与中心化物化：
        - 写入前物化时，虚属性覆盖已持久化内容
        - `labels` 会与已有标签取并集
        """
        return None

    def get_virtual_neighbors(self, key: str) -> list:
        """返回虚实体的逻辑邻居。

        中心层会沿这个邻接关系做闭包物化。
        所以这里应只返回“结构上必要”的邻居，不要返回过大的噪声集合。
        """
        return []

    def bind_src(self, node: dict):
        """给节点绑定 `src` 原生端口。

        返回：
        - `SrcHandle`
        - 或 `None`
        """
        return None

    def match_query(self, node: dict) -> MatchQuery | None:
        """返回主图复用规则。

        这是模块最重要的匹配钩子。

        语义：
        - 给一个虚节点
        - 返回一条 cypher
        - 让中心层去主图里找“是否已经有对应的持久化实体”

        约束：
        - 读查询只构造 merged view：命中 0 个时保留为虚实体，不创建持久实体
        - 写查询触发物化：命中 0 个时没有可合并的持久实体，才创建持久 overlay
        - 命中 1 个：把虚实体属性合并到该持久实体
        - 命中多个：中心层不会自动猜，也不会自动合并
        """
        return None

    def meta_fallback(self, ref: str, include_props=None, _visiting=None) -> dict | None:
        """当主图中没有实体时，按模块规则直接生成一份可读元数据。

        这是只读 fallback，不等于持久化。
        """
        return None
