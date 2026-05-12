"""CSV schema module — expose CSV/TSV columns as virtual graph nodes."""

from __future__ import annotations

import csv
import os
from typing import Dict, Optional

from storage.stores.base import MatchQuery, StoreModule


def _node_copy(node: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in node.items()}


def _safe_col_name(name: str) -> str:
    return (name or "").replace("/", "_").replace("\\", "_").replace(".", "_")


def _infer_type(sample_rows: list[list[str]], col_idx: int) -> str:
    values = []
    for row in sample_rows:
        if col_idx < len(row):
            value = row[col_idx].strip()
            if value:
                values.append(value)
    if not values:
        return "TEXT"

    all_int = True
    all_float = True
    for value in values:
        if all_int:
            try:
                int(value)
            except ValueError:
                all_int = False
        if all_float:
            try:
                float(value)
            except ValueError:
                all_float = False
        if not all_int and not all_float:
            break
    if all_int:
        return "INT"
    if all_float:
        return "FLOAT"
    return "TEXT"


class CSVSchemaModule(StoreModule):
    name = "csv_schema"

    def __init__(self, store):
        self.store = store
        self._bundle_cache: Dict[str, tuple[tuple[int, int], dict]] = {}

    def iter_virtual_nodes(self) -> list[dict]:
        nodes: list[dict] = []
        for csv_rel in self._iter_csv_files():
            bundle = self._bundle_for_csv(csv_rel)
            nodes.extend(_node_copy(n) for n in bundle["nodes"])
        return nodes

    def iter_virtual_edges(self, nodes: list[dict]) -> list[tuple[str, str]]:
        edges: list[tuple[str, str]] = []
        for csv_rel in self._iter_csv_files():
            bundle = self._bundle_for_csv(csv_rel)
            edges.extend(list(bundle["edges"]))
        return edges

    def get_virtual_meta(self, key: str) -> Optional[dict]:
        for csv_rel in self._candidate_csv_paths(key):
            bundle = self._bundle_for_csv(csv_rel)
            meta = bundle["by_lookup"].get(key) or bundle["by_lookup"].get(
                self._canonical_key(key, csv_rel)
            )
            if meta:
                return _node_copy(meta)
        return None

    def get_virtual_neighbors(self, key: str) -> list:
        for csv_rel in self._candidate_csv_paths(key):
            bundle = self._bundle_for_csv(csv_rel)
            canonical = self._canonical_key(key, csv_rel)
            if canonical in bundle["neighbors"]:
                return list(bundle["neighbors"][canonical])
        return []

    def match_query(self, node: dict) -> MatchQuery | None:
        labels = set(node.get("labels", []) or [])
        ref = node.get("ref", "")
        if "col" not in labels or not ref:
            return None
        return MatchQuery(
            query="MATCH (c:col) WHERE c.ref = $ref RETURN c",
            params={"ref": ref},
            var="c",
        )

    def meta_fallback(self, ref: str, include_props=None, _visiting=None) -> dict | None:
        meta = self.get_virtual_meta(ref)
        if not meta:
            return None
        if include_props is None:
            return meta
        return {
            k: v for k, v in meta.items()
            if k in include_props or k in ("name", "labels", "ref")
        }

    def _iter_csv_files(self) -> list[str]:
        project_path = self.store.project_path
        if not project_path:
            return []
        results: list[str] = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                if not fname.lower().endswith((".csv", ".tsv")):
                    continue
                full = os.path.join(root, fname)
                if self._is_backend_file(full):
                    continue
                results.append(os.path.relpath(full, project_path))
        return sorted(set(results))

    def _is_backend_file(self, fp: str) -> bool:
        backend_db = getattr(self.store, "_backend_db_path", None)
        if not backend_db:
            return False
        absp = os.path.abspath(fp)
        db_base = os.path.abspath(backend_db)
        return absp == db_base or absp.startswith(db_base + "-")

    def _bundle_for_csv(self, csv_rel: str) -> dict:
        full = os.path.join(self.store.project_path, csv_rel)
        try:
            stat = os.stat(full)
        except OSError:
            return {"nodes": [], "edges": [], "by_lookup": {}, "neighbors": {}}
        sig = (stat.st_mtime_ns, stat.st_size)
        cached = self._bundle_cache.get(csv_rel)
        if cached and cached[0] == sig:
            return cached[1]
        bundle = self._build_bundle(csv_rel)
        self._bundle_cache[csv_rel] = (sig, bundle)
        return bundle

    def _build_bundle(self, csv_rel: str) -> dict:
        csv_name = os.path.basename(csv_rel)
        delimiter = "\t" if csv_rel.lower().endswith(".tsv") else ","
        nodes: list[dict] = []
        edges: list[tuple[str, str]] = []
        by_lookup: dict[str, dict] = {}
        neighbors: dict[str, list[str]] = {}

        def register(node: dict, aliases: list[str] | None = None):
            nodes.append(node)
            keys = {node.get("ref")}
            for alias in aliases or []:
                keys.add(alias)
            for key in keys:
                if not key:
                    continue
                by_lookup[key] = node
                neighbors.setdefault(key, [])

        def link(a_keys: list[str], a_ref: str, b_ref: str):
            edges.append((a_ref, b_ref))
            for key in a_keys:
                neighbors.setdefault(key, []).append(b_ref)

        full = os.path.join(self.store.project_path, csv_rel)
        try:
            with open(full, "r", encoding="utf-8", errors="ignore", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                headers = next(reader, None) or []
                sample_rows = []
                for idx, row in enumerate(reader):
                    if idx >= 100:
                        break
                    sample_rows.append(row)
        except Exception:
            return {"nodes": [], "edges": [], "by_lookup": {}, "neighbors": {}}

        for idx, raw_col in enumerate(headers):
            col_name = _safe_col_name(raw_col)
            if not col_name:
                col_name = f"column_{idx + 1}"
            col_type = _infer_type(sample_rows, idx)
            col_ref = f"{csv_rel}--{col_name}"
            node = {
                "name": col_name,
                "ref": col_ref,
                "source_column": raw_col,
                "ordinal": idx,
                "col_type": col_type,
                "labels": ["col", col_type],
            }
            register(node, aliases=[
                f"{csv_name}/{col_name}",
                f"{csv_rel}/{col_name}",
            ])
            link([csv_rel, csv_name], csv_rel, col_ref)

        return {"nodes": nodes, "edges": edges, "by_lookup": by_lookup, "neighbors": neighbors}

    def _candidate_csv_paths(self, key: str) -> list[str]:
        csv_files = self._iter_csv_files()
        if key in csv_files:
            return [key]
        if "--" in key:
            head = key.split("--", 1)[0]
            return [
                rel for rel in csv_files
                if rel == head or os.path.basename(rel) == os.path.basename(head)
            ]
        if "/" in key:
            head = key.split("/", 1)[0]
            return [rel for rel in csv_files if os.path.basename(rel) == head or rel == head]
        basename = os.path.basename(key)
        return [rel for rel in csv_files if os.path.basename(rel) == basename or rel == key]

    def _canonical_key(self, key: str, csv_rel: str) -> str:
        if key == csv_rel or key == os.path.basename(csv_rel):
            return key
        if "--" in key:
            if key.startswith(csv_rel + "--"):
                return key
            return f"{csv_rel}--{key.split('--', 1)[1]}"
        return key


__all__ = ["CSVSchemaModule"]
