"""CSV schema module — expose CSV/TSV columns as virtual graph nodes."""

from __future__ import annotations

import csv
import os
from typing import Dict

from storage.stores.base import (
    CypherStatement,
    ModuleContext,
    StoreModule,
    cypher_label_clause,
)


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
    query_labels = {"col"}

    def __init__(self, ctx: ModuleContext):
        super().__init__(ctx)
        self._bundle_cache: Dict[str, tuple[tuple[int, int], dict]] = {}

    @property
    def project_path(self) -> str:
        return self.ctx.source.root

    def should_materialize_for_query(self, parsed, raw_query: str = "") -> bool:
        for node in getattr(parsed, "nodes", []) or []:
            if set(getattr(node, "labels", []) or []) & self.query_labels:
                return True
        return False

    def source_fingerprint(self) -> str | None:
        if not self.project_path:
            return None
        parts = []
        for rel in self._iter_csv_files():
            try:
                stat = self.ctx.source.stat(rel)
            except OSError:
                continue
            parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(parts)

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

    def cypher_statements(self) -> list[CypherStatement]:
        nodes = self.iter_virtual_nodes()
        edges = self.iter_virtual_edges(nodes)
        statements: list[CypherStatement] = []

        grouped: dict[tuple[str, ...], list[dict]] = {}
        for node in nodes:
            labels = tuple(node.get("labels", []) or [])
            props = {k: v for k, v in node.items() if k != "labels" and v is not None}
            grouped.setdefault(labels, []).append({
                "_ref": node.get("_ref"),
                "labels": list(labels),
                "props": props,
            })

        for labels, rows in grouped.items():
            statements.append(CypherStatement(
                query=(
                    "UNWIND $rows AS row "
                    "MATCH (n {ref: row._ref}) "
                    "WHERE n._ref IS NULL "
                    "SET n._ref = row._ref "
                    "REMOVE n.ref"
                ),
                params={"rows": rows},
            ))
            statements.append(CypherStatement(
                query=(
                    "UNWIND $rows AS row "
                    "MERGE (n {_ref: row._ref}) "
                    "ON CREATE SET n.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8) "
                    "ON MATCH SET n.id = coalesce(n.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)) "
                    "SET n += row.props "
                    "REMOVE n.src, n.ref, n.csv_column_handle, n.csv_column_open "
                    "WITH n, row.labels AS labels "
                    "SET n.labels = reduce(acc = [], label IN coalesce(n.labels, []) + labels | "
                    "CASE WHEN label IN acc THEN acc ELSE acc + label END) "
                    f"SET n{cypher_label_clause(list(labels))}"
                ),
                params={"rows": rows},
            ))

        if edges:
            statements.append(CypherStatement(
                query=(
                    "UNWIND $edges AS edge "
                    "MATCH (b) WHERE b._ref = edge.b OR b.ref = edge.b "
                    "OPTIONAL MATCH (a {path: edge.a}) "
                    "FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | "
                    "MERGE (a)-[:RELATED_TO]->(b))"
                ),
                params={
                    "edges": [{"a": a, "b": b} for a, b in edges],
                },
            ))

        return statements

    def _iter_csv_files(self) -> list[str]:
        project_path = self.project_path
        if not project_path:
            return []
        results: list[str] = []
        for root, dirs, files in self.ctx.source.walk():
            dirs[:] = [d for d in dirs if d != ".pontis"]
            for fname in files:
                if not fname.lower().endswith((".csv", ".tsv")):
                    continue
                results.append(os.path.join(root, fname) if root else fname)
        return sorted(set(results))

    def _bundle_for_csv(self, csv_rel: str) -> dict:
        try:
            stat = self.ctx.source.stat(csv_rel)
        except OSError:
            return {"nodes": [], "edges": []}
        sig = (stat.st_mtime_ns, stat.st_size)
        cached = self._bundle_cache.get(csv_rel)
        if cached and cached[0] == sig:
            return cached[1]
        bundle = self._build_bundle(csv_rel)
        self._bundle_cache[csv_rel] = (sig, bundle)
        return bundle

    def _build_bundle(self, csv_rel: str) -> dict:
        delimiter = "\t" if csv_rel.lower().endswith(".tsv") else ","
        nodes: list[dict] = []
        edges: list[tuple[str, str]] = []

        def register(node: dict):
            nodes.append(node)

        def link(a_ref: str, b_ref: str):
            edges.append((a_ref, b_ref))

        try:
            with self.ctx.source.open(csv_rel, "r", encoding="utf-8", errors="ignore", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                headers = next(reader, None) or []
                sample_rows = []
                for idx, row in enumerate(reader):
                    if idx >= 100:
                        break
                    sample_rows.append(row)
        except Exception:
            return {"nodes": [], "edges": []}

        for idx, raw_col in enumerate(headers):
            col_name = _safe_col_name(raw_col)
            if not col_name:
                col_name = f"column_{idx + 1}"
            col_type = _infer_type(sample_rows, idx)
            col_ref = f"{csv_rel}--{col_name}"
            node = {
                "name": col_name,
                "_ref": col_ref,
                "source_column": raw_col,
                "ordinal": idx,
                "col_type": col_type,
                "labels": ["col", col_type],
            }
            register(node)
            link(csv_rel, col_ref)

        return {"nodes": nodes, "edges": edges}
__all__ = ["CSVSchemaModule"]
