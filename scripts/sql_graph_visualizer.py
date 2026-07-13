#!/usr/bin/env python3
"""Render SQL parsing diagrams as SVG images.

This script shows the same SQL from two perspectives:

1. AST / syntax tree: compiler-style parse structure.
2. Operator graph: relational-operation view used for validation/reward/rerank.

Usage:
    uv run python scripts/sql_graph_visualizer.py \
      --sql "SELECT ..." \
      --out-dir docs/assets/sql_graph_demo \
      --name demo

Outputs:
    demo_ast.svg
    demo_operator.svg
    demo_pipeline.svg
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import sqlglot
from sqlglot import exp


PALETTE = {
    "bg": "#f8fafc",
    "panel": "#ffffff",
    "ink": "#0f172a",
    "muted": "#475569",
    "edge": "#64748b",
    "ast": "#dbeafe",
    "ast_border": "#2563eb",
    "op": "#dcfce7",
    "op_border": "#16a34a",
    "sql": "#fef3c7",
    "sql_border": "#d97706",
    "cte": "#fae8ff",
    "cte_border": "#c026d3",
}


@dataclass
class DrawNode:
    id: str
    lines: list[str]
    layer: int = 0
    x: float = 0
    y: float = 0
    width: float = 170
    height: float = 64
    fill: str = PALETTE["panel"]
    stroke: str = PALETTE["edge"]


@dataclass
class DrawEdge:
    src: str
    dst: str
    label: str = ""
    dashed: bool = False


@dataclass
class AstTree:
    id: str
    lines: list[str]
    edge_label: str = ""
    children: list["AstTree"] = field(default_factory=list)
    x: float = 0
    y: float = 0
    leaves: int = 1


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def next(self, prefix: str = "n") -> str:
        self.value += 1
        return f"{prefix}{self.value}"


def wrap_lines(text: str, width: int = 28, max_lines: int = 5) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return [""]
    lines: list[str] = []
    for part in text.split("\n"):
        lines.extend(textwrap.wrap(part, width=width) or [""])
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [lines[max_lines - 1][: width - 3] + "..."]
    return lines


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def expression_kind(e: exp.Expression) -> str:
    return e.key.upper() if getattr(e, "key", None) else e.__class__.__name__


def expression_label(e: exp.Expression, dialect: str) -> list[str]:
    kind = expression_kind(e)
    if isinstance(e, exp.Table):
        return ["TABLE", e.sql(dialect=dialect)]
    if isinstance(e, exp.Column):
        return ["COLUMN", e.sql(dialect=dialect)]
    if isinstance(e, exp.Identifier):
        return ["IDENT", str(e.this)]
    if isinstance(e, exp.Literal):
        return ["LITERAL", e.sql(dialect=dialect)]
    if isinstance(e, exp.Alias):
        return ["ALIAS", e.alias_or_name or ""]
    if isinstance(e, exp.CTE):
        return ["CTE", e.alias_or_name or ""]
    if isinstance(e, exp.Join):
        side = e.args.get("side")
        return ["JOIN" if not side else f"{side} JOIN"]
    if isinstance(e, exp.Select):
        return ["SELECT"]
    if isinstance(e, exp.Where):
        return ["WHERE"]
    if isinstance(e, exp.Group):
        return ["GROUP BY"]
    if isinstance(e, exp.Order):
        return ["ORDER BY"]
    sql = e.sql(dialect=dialect)
    if len(sql) <= 42 and not isinstance(e, (exp.And, exp.Or)):
        return [kind, sql]
    return [kind]


def iter_expression_children(e: exp.Expression) -> Iterable[tuple[str, exp.Expression]]:
    for arg_name, value in e.args.items():
        if value is None:
            continue
        if isinstance(value, exp.Expression):
            yield arg_name, value
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, exp.Expression):
                    yield f"{arg_name}[{i}]", item


def build_ast_tree(
    e: exp.Expression,
    ids: Ids,
    dialect: str,
    *,
    edge_label: str = "",
    depth: int = 0,
    max_depth: int = 7,
    max_nodes: int = 180,
) -> AstTree:
    node = AstTree(ids.next("ast"), expression_label(e, dialect), edge_label=edge_label)
    if ids.value >= max_nodes:
        node.children.append(AstTree(ids.next("ast"), ["..."], edge_label="truncated"))
        return node
    if depth >= max_depth:
        if any(True for _ in iter_expression_children(e)):
            node.children.append(AstTree(ids.next("ast"), ["..."], edge_label="deeper"))
        return node
    for child_label, child in iter_expression_children(e):
        node.children.append(
            build_ast_tree(
                child,
                ids,
                dialect,
                edge_label=child_label,
                depth=depth + 1,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
        )
    return node


def layout_ast(root: AstTree, *, h_gap: float = 190, v_gap: float = 120, margin: float = 48) -> tuple[float, float]:
    leaf_index = 0

    def walk(node: AstTree, depth: int) -> None:
        nonlocal leaf_index
        node.y = margin + depth * v_gap
        if not node.children:
            node.x = margin + leaf_index * h_gap
            node.leaves = 1
            leaf_index += 1
            return
        for child in node.children:
            walk(child, depth + 1)
        node.x = sum(child.x for child in node.children) / len(node.children)
        node.leaves = sum(child.leaves for child in node.children)

    walk(root, 0)
    max_depth = 0

    def depth_walk(node: AstTree, depth: int) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        for child in node.children:
            depth_walk(child, depth + 1)

    depth_walk(root, 0)
    width = max(900, margin * 2 + max(0, leaf_index - 1) * h_gap + 180)
    height = margin * 2 + (max_depth + 1) * v_gap
    return width, height


def ast_to_draw(root: AstTree) -> tuple[list[DrawNode], list[DrawEdge]]:
    nodes: list[DrawNode] = []
    edges: list[DrawEdge] = []

    def walk(node: AstTree) -> None:
        height = 42 + max(0, len(node.lines) - 1) * 16
        nodes.append(
            DrawNode(
                id=node.id,
                lines=node.lines,
                x=node.x,
                y=node.y,
                width=150,
                height=max(54, height),
                fill=PALETTE["ast"],
                stroke=PALETTE["ast_border"],
            )
        )
        for child in node.children:
            edges.append(DrawEdge(node.id, child.id, child.edge_label))
            walk(child)

    walk(root)
    return nodes, edges


def table_label(table: exp.Table, dialect: str) -> str:
    alias = table.alias_or_name
    name = table.name or table.sql(dialect=dialect)
    if alias and alias != name:
        return f"{name} AS {alias}"
    return name


def short_sql(e: exp.Expression | None, dialect: str, width: int = 56) -> str:
    if e is None:
        return ""
    value = e.sql(dialect=dialect)
    value = re.sub(r"\s+", " ", value.strip())
    if len(value) > width:
        value = value[: width - 3] + "..."
    return value


def direct_from_table(select: exp.Select) -> exp.Expression | None:
    from_ = select.args.get("from_")
    if isinstance(from_, exp.From):
        return from_.this
    return None


def direct_join_tables(select: exp.Select) -> list[tuple[exp.Expression, exp.Expression | None]]:
    joins = select.args.get("joins") or []
    result: list[tuple[exp.Expression, exp.Expression | None]] = []
    for join in joins:
        if isinstance(join, exp.Join):
            result.append((join.this, join.args.get("on")))
    return result


def select_projection_summary(select: exp.Select, dialect: str) -> str:
    exprs = select.args.get("expressions") or []
    if not exprs:
        return "*"
    return ", ".join(short_sql(e, dialect, 24) for e in exprs[:4]) + (" ..." if len(exprs) > 4 else "")


def aggregate_summary(select: exp.Select, dialect: str) -> str:
    exprs = select.args.get("expressions") or []
    aggs: list[str] = []
    for item in exprs:
        if any(isinstance(a, exp.AggFunc) for a in item.walk()):
            aggs.append(short_sql(item, dialect, 32))
    return ", ".join(aggs[:3]) + (" ..." if len(aggs) > 3 else "")


def build_operator_graph(root: exp.Expression, dialect: str) -> tuple[list[DrawNode], list[DrawEdge]]:
    ids = Ids()
    nodes: list[DrawNode] = []
    edges: list[DrawEdge] = []
    cte_outputs: dict[str, str] = {}
    layer_cursor = 0

    def add_node(lines: list[str], layer: int, fill: str = PALETTE["op"], stroke: str = PALETTE["op_border"]) -> str:
        node_id = ids.next("op")
        height = 44 + max(0, len(lines) - 1) * 17
        nodes.append(DrawNode(node_id, lines, layer=layer, height=max(60, height), fill=fill, stroke=stroke))
        return node_id

    def build_select(select: exp.Select, scope: str, base_layer: int, final_name: str | None = None) -> tuple[str, int]:
        source = direct_from_table(select)
        current: str | None = None
        current_layer = base_layer

        if isinstance(source, exp.Table):
            table_name = source.name
            prefix = "Read CTE" if table_name in cte_outputs else "Scan"
            current = add_node([prefix, table_label(source, dialect)], current_layer)
            if table_name in cte_outputs:
                edges.append(DrawEdge(cte_outputs[table_name], current, "materialized", dashed=True))
        elif source is not None:
            current = add_node(["Source", short_sql(source, dialect, 40)], current_layer)
        else:
            current = add_node(["Source", "single row"], current_layer)

        for join_source, on_expr in direct_join_tables(select):
            scan_label = short_sql(join_source, dialect, 36)
            scan = add_node(["Scan", scan_label], base_layer)
            current_layer += 1
            join_lines = ["Join"]
            if on_expr is not None:
                join_lines.extend(wrap_lines(short_sql(on_expr, dialect, 64), 26, 3))
            join = add_node(join_lines, current_layer)
            edges.append(DrawEdge(current, join, "left"))
            edges.append(DrawEdge(scan, join, "right"))
            current = join

        where = select.args.get("where")
        if isinstance(where, exp.Where):
            current_layer += 1
            filt = add_node(["Filter"] + wrap_lines(short_sql(where.this, dialect, 72), 30, 3), current_layer)
            edges.append(DrawEdge(current, filt))
            current = filt

        group = select.args.get("group")
        agg = aggregate_summary(select, dialect)
        if isinstance(group, exp.Group) or agg:
            current_layer += 1
            group_lines = ["Group/Aggregate"]
            if isinstance(group, exp.Group):
                group_lines.extend(wrap_lines("keys: " + short_sql(group, dialect, 64), 30, 2))
            if agg:
                group_lines.extend(wrap_lines("aggs: " + agg, 30, 2))
            group_node = add_node(group_lines, current_layer)
            edges.append(DrawEdge(current, group_node))
            current = group_node

        having = select.args.get("having")
        if isinstance(having, exp.Having):
            current_layer += 1
            having_node = add_node(["Having"] + wrap_lines(short_sql(having.this, dialect, 72), 30, 3), current_layer)
            edges.append(DrawEdge(current, having_node))
            current = having_node

        current_layer += 1
        project = add_node(["Project"] + wrap_lines(select_projection_summary(select, dialect), 30, 3), current_layer)
        edges.append(DrawEdge(current, project))
        current = project

        order = select.args.get("order")
        if isinstance(order, exp.Order):
            current_layer += 1
            order_node = add_node(["Sort"] + wrap_lines(short_sql(order, dialect, 72), 30, 3), current_layer)
            edges.append(DrawEdge(current, order_node))
            current = order_node

        limit = select.args.get("limit")
        if isinstance(limit, exp.Limit):
            current_layer += 1
            limit_node = add_node(["Limit"] + wrap_lines(short_sql(limit, dialect, 36), 30, 2), current_layer)
            edges.append(DrawEdge(current, limit_node))
            current = limit_node

        if final_name:
            current_layer += 1
            final = add_node(["CTE result", final_name], current_layer, fill=PALETTE["cte"], stroke=PALETTE["cte_border"])
            edges.append(DrawEdge(current, final))
            current = final
            cte_outputs[final_name] = final
        else:
            nodes_by_id = {n.id: n for n in nodes}
            nodes_by_id[current].lines[0] = f"{scope} result"

        return current, current_layer

    if isinstance(root, exp.Select):
        with_ = root.args.get("with_")
        if isinstance(with_, exp.With):
            for cte in with_.expressions:
                if isinstance(cte, exp.CTE) and isinstance(cte.this, exp.Select):
                    _, end_layer = build_select(cte.this, f"CTE {cte.alias_or_name}", layer_cursor, cte.alias_or_name)
                    layer_cursor = end_layer + 2
        build_select(root, "Final", layer_cursor)
    else:
        root_select = next(root.find_all(exp.Select), None)
        if root_select is not None:
            build_select(root_select, "Final", layer_cursor)
        else:
            add_node(["Unsupported SQL", root.__class__.__name__], 0)

    layout_layered(nodes)
    return nodes, edges


def layout_layered(nodes: list[DrawNode], *, x_gap: float = 240, y_gap: float = 120, margin: float = 64) -> tuple[float, float]:
    layers: dict[int, list[DrawNode]] = {}
    for node in nodes:
        layers.setdefault(node.layer, []).append(node)
    for layer, layer_nodes in layers.items():
        for row, node in enumerate(layer_nodes):
            node.x = margin + layer * x_gap
            node.y = margin + row * y_gap
    width = margin * 2 + (max(layers) + 1) * x_gap if layers else 900
    max_rows = max((len(v) for v in layers.values()), default=1)
    height = margin * 2 + max_rows * y_gap
    return max(width, 900), max(height, 420)


def draw_svg(nodes: list[DrawNode], edges: list[DrawEdge], title: str, path: Path) -> None:
    max_x = max((n.x + n.width for n in nodes), default=800)
    max_y = max((n.y + n.height for n in nodes), default=400)
    width = math.ceil(max_x + 72)
    height = math.ceil(max_y + 72)
    by_id = {n.id: n for n in nodes}
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">',
        f'<path d="M0,0 L0,6 L9,3 z" fill="{PALETTE["edge"]}" />',
        "</marker>",
        "</defs>",
        f'<rect width="100%" height="100%" fill="{PALETTE["bg"]}"/>',
        f'<text x="32" y="34" font-family="Inter,Arial,sans-serif" font-size="22" font-weight="700" fill="{PALETTE["ink"]}">{escape(title)}</text>',
    ]

    for edge in edges:
        src = by_id[edge.src]
        dst = by_id[edge.dst]
        x1 = src.x + src.width / 2
        y1 = src.y + src.height
        x2 = dst.x + dst.width / 2
        y2 = dst.y
        if abs(dst.y - src.y) < 40:
            x1 = src.x + src.width
            y1 = src.y + src.height / 2
            x2 = dst.x
            y2 = dst.y + dst.height / 2
        dash = ' stroke-dasharray="6 5"' if edge.dashed else ""
        parts.append(
            f'<path d="M{x1:.1f},{y1:.1f} C{x1:.1f},{(y1 + y2) / 2:.1f} {x2:.1f},{(y1 + y2) / 2:.1f} {x2:.1f},{y2:.1f}" '
            f'fill="none" stroke="{PALETTE["edge"]}" stroke-width="1.6"{dash} marker-end="url(#arrow)"/>'
        )
        if edge.label:
            lx = (x1 + x2) / 2
            ly = (y1 + y2) / 2 - 4
            parts.append(
                f'<text x="{lx:.1f}" y="{ly:.1f}" font-family="Inter,Arial,sans-serif" font-size="11" '
                f'text-anchor="middle" fill="{PALETTE["muted"]}">{escape(edge.label)}</text>'
            )

    for node in nodes:
        parts.append(
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" height="{node.height:.1f}" '
            f'rx="10" fill="{node.fill}" stroke="{node.stroke}" stroke-width="1.8"/>'
        )
        y = node.y + 22
        for i, line in enumerate(node.lines):
            size = 13 if i else 14
            weight = "700" if i == 0 else "500"
            color = PALETTE["ink"] if i == 0 else PALETTE["muted"]
            parts.append(
                f'<text x="{node.x + node.width / 2:.1f}" y="{y:.1f}" font-family="Inter,Arial,sans-serif" '
                f'font-size="{size}" font-weight="{weight}" text-anchor="middle" fill="{color}">{escape(line)}</text>'
            )
            y += 16

    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def draw_pipeline(sql: str, tree: exp.Expression, dialect: str, path: Path) -> None:
    width, height = 1280, 620
    ast_items = []
    for key in ["with_", "expressions", "from_", "joins", "where", "group", "having", "order", "limit"]:
        if tree.args.get(key):
            ast_items.append(key.upper().replace("_", ""))
    op_items = ["Scan tables", "Join relations", "Filter rows", "Group/Aggregate", "Project output"]
    if tree.args.get("order"):
        op_items.append("Sort")
    if tree.args.get("limit"):
        op_items.append("Limit")

    def card(x: int, y: int, w: int, h: int, title: str, lines: list[str], fill: str, stroke: str) -> list[str]:
        out = [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="2"/>',
            f'<text x="{x + 24}" y="{y + 36}" font-family="Inter,Arial,sans-serif" font-size="22" font-weight="800" fill="{PALETTE["ink"]}">{escape(title)}</text>',
        ]
        yy = y + 72
        for line in lines:
            out.append(
                f'<text x="{x + 24}" y="{yy}" font-family="Inter,Arial,sans-serif" font-size="15" font-weight="500" fill="{PALETTE["muted"]}">{escape(line)}</text>'
            )
            yy += 24
        return out

    sql_lines = wrap_lines(re.sub(r"\s+", " ", sql), 42, 14)
    ast_lines = ["Compiler view: SQL text -> parse tree", "Main nodes:"] + [f"- {item}" for item in ast_items[:9]]
    op_lines = ["Relational view: SQL -> data operations", "Typical operators:"] + [f"- {item}" for item in op_items]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="arrow2" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">',
        f'<path d="M0,0 L0,6 L9,3 z" fill="{PALETTE["edge"]}" />',
        "</marker>",
        "</defs>",
        f'<rect width="100%" height="100%" fill="{PALETTE["bg"]}"/>',
        f'<text x="40" y="46" font-family="Inter,Arial,sans-serif" font-size="26" font-weight="800" fill="{PALETTE["ink"]}">SQL to Graph: parsing process</text>',
    ]
    parts.extend(card(40, 90, 350, 440, "1. SQL text", sql_lines, PALETTE["sql"], PALETTE["sql_border"]))
    parts.extend(card(465, 90, 350, 440, "2. AST / syntax tree", ast_lines, PALETTE["ast"], PALETTE["ast_border"]))
    parts.extend(card(890, 90, 350, 440, "3. Operator graph", op_lines, PALETTE["op"], PALETTE["op_border"]))
    parts.append(f'<path d="M405,310 L450,310" stroke="{PALETTE["edge"]}" stroke-width="3" marker-end="url(#arrow2)"/>')
    parts.append(f'<path d="M830,310 L875,310" stroke="{PALETTE["edge"]}" stroke-width="3" marker-end="url(#arrow2)"/>')
    parts.append(
        f'<text x="640" y="570" font-family="Inter,Arial,sans-serif" font-size="16" text-anchor="middle" fill="{PALETTE["muted"]}">'
        "AST explains how SQL text is structured; the operator graph explains what the SQL does to data."
        "</text>"
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def read_sql(args: argparse.Namespace) -> str:
    if args.sql:
        return args.sql
    if args.sql_file:
        return Path(args.sql_file).read_text(encoding="utf-8")
    raise SystemExit("Provide --sql or --sql-file.")


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("_")
    return value[:80] or "sql_graph"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SVG diagrams for SQL AST and operator graph.")
    parser.add_argument("--sql", help="SQL string to visualize.")
    parser.add_argument("--sql-file", help="Path to a file containing SQL.")
    parser.add_argument("--dialect", default="sqlite", help="sqlglot dialect, e.g. sqlite, postgres, snowflake.")
    parser.add_argument("--out-dir", default="docs/assets/sql_graph", help="Output directory.")
    parser.add_argument("--name", default="demo", help="Output file prefix.")
    parser.add_argument("--max-ast-depth", type=int, default=7, help="Maximum AST depth to draw.")
    parser.add_argument("--max-ast-nodes", type=int, default=180, help="Maximum AST nodes to draw.")
    parser.add_argument("--json", action="store_true", help="Print output paths as JSON.")
    args = parser.parse_args()

    sql = read_sql(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / slugify(args.name)

    try:
        parsed = sqlglot.parse_one(sql, read=args.dialect)
    except Exception as exc:  # sqlglot raises several parse/token errors.
        raise SystemExit(f"Could not parse SQL with dialect={args.dialect}: {exc}") from exc

    ast_root = build_ast_tree(parsed, Ids(), args.dialect, max_depth=args.max_ast_depth, max_nodes=args.max_ast_nodes)
    layout_ast(ast_root)
    ast_nodes, ast_edges = ast_to_draw(ast_root)
    ast_path = prefix.with_name(prefix.name + "_ast.svg")
    draw_svg(ast_nodes, ast_edges, "SQL AST / Syntax Tree", ast_path)

    op_nodes, op_edges = build_operator_graph(parsed, args.dialect)
    op_path = prefix.with_name(prefix.name + "_operator.svg")
    draw_svg(op_nodes, op_edges, "SQL Semantic / Operator Graph", op_path)

    pipeline_path = prefix.with_name(prefix.name + "_pipeline.svg")
    draw_pipeline(sql, parsed, args.dialect, pipeline_path)

    outputs = {
        "ast": str(ast_path),
        "operator": str(op_path),
        "pipeline": str(pipeline_path),
    }
    if args.json:
        print(json.dumps(outputs, ensure_ascii=False, indent=2))
    else:
        for key, value in outputs.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
