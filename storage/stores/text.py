"""Text source module.

Text is intentionally separate from `fs`: filesystem labels come from suffixes,
while this module decides whether a file should also be projected as `text` and
adds lightweight text metadata.
"""

from __future__ import annotations

from datetime import datetime
import os
from typing import Optional

from storage.stores.base import (
    CypherStatement,
    ModuleContext,
    StoreModule,
    cypher_label_clause,
)
from storage.stores.utils.text import is_text_file


MAX_TEXT_META_BYTES = 2 * 1024 * 1024


class TextModule(StoreModule):
    name = "text"
    query_labels = {"text"}

    def __init__(self, ctx: ModuleContext):
        super().__init__(ctx)
        self._meta_cache: dict[str, tuple[tuple[int, int], dict]] = {}

    @property
    def project_path(self) -> str:
        return self.ctx.source.root

    def should_materialize_for_query(self, parsed, raw_query: str = "") -> bool:
        for node in getattr(parsed, "nodes", []) or []:
            if set(getattr(node, "labels", []) or []) & self.query_labels:
                return True
        return False

    def iter_virtual_nodes(self) -> list[dict]:
        nodes = []
        for rel in self._iter_text_files():
            meta = self.get_virtual_meta(rel)
            if meta is not None:
                nodes.append({"name": os.path.basename(rel), **meta})
        return nodes

    def cypher_statements(self) -> list[CypherStatement]:
        nodes = []
        for node in self.iter_virtual_nodes():
            labels = list(node.get("labels", []) or [])
            props = {k: v for k, v in node.items() if k != "labels" and v is not None}
            nodes.append({
                "path": node.get("path"),
                "labels": labels,
                "props": props,
            })
        if not nodes:
            return []
        return [CypherStatement(
            query=(
                "UNWIND $rows AS row "
                "MERGE (n {path: row.path}) "
                "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
                "ON MATCH SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)) "
                "SET n += row.props "
                "REMOVE n.text_src, n.text_handle, n.text_open "
                "WITH n, row.labels AS labels "
                "SET n.labels = reduce(acc = [], label IN coalesce(n.labels, []) + labels | "
                "CASE WHEN label IN acc THEN acc ELSE acc + label END) "
                f"SET n{cypher_label_clause(['file', 'text'])}"
            ),
            params={"rows": nodes},
        )]

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        if not self.project_path:
            return None
        full = self.ctx.source.absolute_path(key)
        if not os.path.isfile(full) or not is_text_file(os.path.basename(full)):
            return None
        try:
            stat = os.stat(full)
        except OSError:
            return None

        meta = {
            "path": key,
            "labels": ["file", "text"],
            "file_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
        meta.update(self._text_meta(key, stat))
        return meta

    def _iter_text_files(self) -> list[str]:
        if not self.project_path:
            return []
        results = []
        for root, dirs, files in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                if is_text_file(fname):
                    results.append(os.path.join(root, fname) if root else fname)
        return sorted(set(results))

    def _text_meta(self, rel_path: str, stat: os.stat_result) -> dict:
        sig = (stat.st_mtime_ns, stat.st_size)
        cached = self._meta_cache.get(rel_path)
        if cached and cached[0] == sig:
            return dict(cached[1])
        if stat.st_size > MAX_TEXT_META_BYTES:
            return {}

        full = self.ctx.source.absolute_path(rel_path)
        encoding = self._detect_encoding(full)
        try:
            with open(full, "r", encoding=encoding, errors="ignore") as f:
                content = f.read()
        except OSError:
            return {}

        result = {
            "encoding": encoding,
            "line_count": len(content.splitlines()),
            "char_count": len(content),
        }
        self._meta_cache[rel_path] = (sig, result)
        return dict(result)

    @staticmethod
    def _detect_encoding(path: str) -> str:
        try:
            import chardet
        except ImportError:
            return "utf-8"
        try:
            with open(path, "rb") as f:
                raw = f.read(10000)
            if not raw:
                return "utf-8"
            result = chardet.detect(raw)
            return result.get("encoding") or "utf-8"
        except Exception:
            return "utf-8"


__all__ = ["TextModule"]
