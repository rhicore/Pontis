"""Source binding — 将图中的 source 节点绑定为原生访问端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SrcHandle:
    """单个 source 节点的原生端口绑定。"""

    node_data: dict
    _ports: Dict[str, object]
    kind_name: str

    @property
    def node(self) -> dict:
        return self.node_data

    def kind(self) -> str:
        return self.kind_name

    def ports(self) -> List[str]:
        return sorted(self._ports.keys())

    def has(self, name: str) -> bool:
        return name in self._ports

    def get(self, name: str):
        if name not in self._ports:
            raise KeyError(f"Port not available: {name}")
        return self._ports[name]
