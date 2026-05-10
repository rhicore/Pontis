"""Graph-only Store — 纯图存储项目，无数据源发现与文件系统虚节点。"""

from __future__ import annotations

from typing import Dict, List, Optional

from storage.store import Store


class GraphStore(Store):
    """纯图存储 Store。

    用于像 `bird` 这类只有 graph、没有 source 数据源的项目。
    """

    def __init__(self, source_config, backend):
        super().__init__(source_config, backend)
        self._project_path = ""
        self._name_index: Dict[str, object] = {}

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def pontis_exists(self) -> bool:
        return True

    @property
    def index_root(self) -> str:
        return ""

    def _register_node(self, ent_id: str, props: dict):
        super()._register_node(ent_id, props)
        ename = props.get("name", "")
        if not ename:
            return
        if ename in self._name_index:
            existing = self._name_index[ename]
            if isinstance(existing, list):
                if ent_id not in existing:
                    existing.append(ent_id)
            elif existing != ent_id:
                self._name_index[ename] = [existing, ent_id]
        else:
            self._name_index[ename] = ent_id

    def _unregister_node(self, ent_id: str):
        props = self._id_index.get(ent_id, {})
        ename = props.get("name", "")
        if ename:
            ent_ids = self._name_index.get(ename)
            if isinstance(ent_ids, list):
                ent_ids = [eid for eid in ent_ids if eid != ent_id]
                if len(ent_ids) == 1:
                    self._name_index[ename] = ent_ids[0]
                elif len(ent_ids) == 0:
                    self._name_index.pop(ename, None)
                else:
                    self._name_index[ename] = ent_ids
            elif ent_ids == ent_id:
                self._name_index.pop(ename, None)
        super()._unregister_node(ent_id)

    def _on_before_persist(self, meta: dict, ename: str):
        meta.setdefault("name", ename)

    def _name_to_id(self, entity_name: str) -> Optional[str]:
        if not self._index_built:
            self._ensure_index()
        ent_ids = self._name_index.get(entity_name)
        if ent_ids is None:
            return None
        if isinstance(ent_ids, list):
            return ent_ids[0]
        return ent_ids

    def _name_to_ids(self, entity_name: str) -> List[str]:
        if not self._index_built:
            self._ensure_index()
        ent_ids = self._name_index.get(entity_name)
        if ent_ids is None:
            return []
        if isinstance(ent_ids, list):
            return list(ent_ids)
        return [ent_ids]

    def _resolve_to_id(self, ref: str) -> Optional[str]:
        if not self._index_built:
            self._ensure_index()
        eid = self._name_to_id(ref)
        if eid:
            return eid
        if ref in self._id_index:
            return ref
        if "--" in ref:
            parts = ref.split("--")
            current_ids = self._name_to_ids(parts[0])
            matched_prefix = bool(current_ids)
            for seg in parts[1:]:
                if not current_ids:
                    break
                next_ids = []
                for current_id in current_ids:
                    for adj_id in self._adjacent.get(current_id, set()):
                        adj_props = self._id_index.get(adj_id, {})
                        if adj_props.get("name") == seg:
                            next_ids.append(adj_id)
                current_ids = next_ids
                if current_ids:
                    matched_prefix = True
            if len(current_ids) == 1:
                return current_ids[0]
            if len(current_ids) > 1:
                return current_ids[0]
            if matched_prefix:
                return None
            return self._name_to_id(parts[-1])
        return None

    def _list_all(self):
        self._ensure_index()
        results = []
        for eid, props in self._id_index.items():
            results.append((props.get("name", eid), props.get("_labels", [])))
        return results

    def _neighbors(self, ref: str):
        self._ensure_index()
        ent_id = self._resolve_to_id(ref)
        if not ent_id:
            return []
        return [
            self._id_index.get(aid, {}).get("name", aid)
            for aid in self._adjacent.get(ent_id, set())
            if self._id_index.get(aid)
        ]

