"""Deterministic schema landscape writer.

This explorer turns ``topic`` / legacy ``topic_family`` / ``table_group`` nodes
into a compact navigation node that a later agent can read before expanding
hundreds of physical tables.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Iterable

from extractor.utils.refs import neo4j_props
from storage.workspace import Workspace

logger = logging.getLogger(__name__)

DEFAULT_TOP_SOURCES = 20
DEFAULT_TOP_TOPICS = 40
DEFAULT_TOP_TABLE_GROUPS = 40


def generate(
    workspace: Workspace,
    *,
    top_sources: int = DEFAULT_TOP_SOURCES,
    top_topics: int = DEFAULT_TOP_TOPICS,
    top_table_groups: int = DEFAULT_TOP_TABLE_GROUPS,
) -> None:
    """Write ``:schema_landscape`` navigation nodes from existing group facts."""

    logger.info("=== Schema landscape explorer ===")
    written = 0
    for project in workspace.active_projects:
        store = workspace._get_store(project)
        if store is None:
            continue
        with store.execution_lock:
            db_nodes = _read_nodes(store, "MATCH (d:db {project: $project}) RETURN d", "d", project)
            if not db_nodes:
                logger.info("  No db nodes found for project %s", project)
                continue

            sources = _read_nodes(
                store,
                """
                MATCH (g:source_collection {project: $project})
                RETURN g
                ORDER BY g.physical_table_count DESC, g.schema_name
                """,
                "g",
                project,
            )
            topics = _read_nodes(
                store,
                """
                MATCH (g {project: $project})
                WHERE g:topic_family OR g:topic
                OPTIONAL MATCH (d:db {project: $project})-[:RELATED_TO*1..3]-(g)
                OPTIONAL MATCH (s:schema {project: $project})--(g)
                OPTIONAL MATCH (g)--(tg:table_group {project: $project})
                OPTIONAL MATCH (g)--(t:table {project: $project})
                WHERE NOT (t)--(:table_group {project: $project})
                WITH g,
                     collect(DISTINCT d._ref) AS db_refs,
                     collect(DISTINCT s._ref) AS schema_refs,
                     collect(DISTINCT s.name) AS schema_names,
                     count(DISTINCT tg) AS table_group_count,
                     count(DISTINCT t) AS standalone_table_count
                SET g.db_ref = coalesce(g.db_ref, head([ref IN db_refs WHERE ref IS NOT NULL])),
                    g._db_ref = coalesce(g._db_ref, head([ref IN db_refs WHERE ref IS NOT NULL])),
                    g.schema_ref = coalesce(g.schema_ref, head([ref IN schema_refs WHERE ref IS NOT NULL])),
                    g.schema_name = coalesce(g.schema_name, head([name IN schema_names WHERE name IS NOT NULL])),
                    g.logical_unit_count = coalesce(g.logical_unit_count, table_group_count + standalone_table_count),
                    g.table_group_count = coalesce(g.table_group_count, table_group_count),
                    g.standalone_table_count = coalesce(g.standalone_table_count, standalone_table_count)
                RETURN g
                ORDER BY coalesce(g.physical_table_count, g.logical_unit_count, 0) DESC, g.schema_name, g.topic_key
                """,
                "g",
                project,
            )
            table_groups = _read_nodes(
                store,
                """
                MATCH (g:table_group {project: $project})
                RETURN g
                ORDER BY g.member_count DESC, g.schema_name, g.family
                """,
                "g",
                project,
            )
            db_counts = _read_counts(store, project)
            _delete_stale_landscapes(store, project)

            sources_by_db = _by_db(sources)
            topics_by_db = _by_db(topics)
            table_groups_by_db = _by_db(table_groups)

            for db in db_nodes:
                db_ref = str(db.get("_ref") or db.get("name") or "")
                if not db_ref:
                    continue
                summary = _landscape_summary(
                    project=project,
                    db=db,
                    counts=db_counts.get(db_ref, {}),
                    sources=sources_by_db.get(db_ref, []),
                    topics=topics_by_db.get(db_ref, []),
                    table_groups=table_groups_by_db.get(db_ref, []),
                    top_sources=max(1, top_sources),
                    top_topics=max(1, top_topics),
                    top_table_groups=max(1, top_table_groups),
                )
                _upsert_landscape(
                    store,
                    project=project,
                    db_ref=db_ref,
                    summary=summary,
                    linked_refs=_linked_refs(
                        sources_by_db.get(db_ref, []),
                        topics_by_db.get(db_ref, []),
                        table_groups_by_db.get(db_ref, []),
                    ),
                )
                written += 1
    logger.info("  Schema landscapes written: %s", written)


def _read_nodes(store, query: str, key: str, project: str) -> list[dict]:
    rows = store.execute_cypher(query, params={"project": project})
    return [row.get(key) or {} for row in rows if row.get(key)]


def _read_counts(store, project: str) -> dict[str, dict]:
    rows = store.execute_cypher(
        """
        MATCH (d:db {project: $project})
        OPTIONAL MATCH (d)-[:RELATED_TO*1..2]-(t:table {project: $project})
        WITH d, collect(DISTINCT t) AS tables
        UNWIND CASE WHEN tables = [] THEN [null] ELSE tables END AS table_node
        OPTIONAL MATCH (table_node)--(c:col {project: $project})
        RETURN d._ref AS db_ref,
               count(DISTINCT table_node) AS table_count,
               count(DISTINCT c) AS column_count
        """,
        params={"project": project},
    )
    return {
        str(row.get("db_ref")): {
            "table_count": int(row.get("table_count") or 0),
            "column_count": int(row.get("column_count") or 0),
        }
        for row in rows
        if row.get("db_ref")
    }


def _by_db(nodes: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        db_ref = str(node.get("db_ref") or node.get("_db_ref") or "")
        if db_ref:
            grouped[db_ref].append(node)
    return grouped


def _delete_stale_landscapes(store, project: str) -> None:
    store.execute_cypher(
        "MATCH (l:schema_landscape {project: $project}) DETACH DELETE l",
        params={"project": project},
    )


def _landscape_summary(
    *,
    project: str,
    db: dict,
    counts: dict,
    sources: list[dict],
    topics: list[dict],
    table_groups: list[dict],
    top_sources: int,
    top_topics: int,
    top_table_groups: int,
) -> dict:
    db_ref = str(db.get("_ref") or db.get("name") or "")
    db_name = str(db.get("database_name") or db.get("name") or db_ref)
    source_lines = [_source_line(item) for item in sources[:top_sources]]
    topic_lines = [_topic_line(item) for item in topics[:top_topics]]
    table_group_lines = [_table_group_line(item) for item in table_groups[:top_table_groups]]
    detail = _markdown_detail(
        db_name=db_name,
        counts=counts,
        sources=source_lines,
        topics=topic_lines,
        table_groups=table_group_lines,
        omitted_sources=max(0, len(sources) - top_sources),
        omitted_topics=max(0, len(topics) - top_topics),
        omitted_table_groups=max(0, len(table_groups) - top_table_groups),
    )
    total_grouped_tables = sum(int(item.get("member_count") or 0) for item in table_groups)
    return {
        "_ref": f"{db_ref}--schema_landscape",
        "_db_ref": db_ref,
        "db_ref": db_ref,
        "name": f"schema_landscape[{db_name}]",
        "brief": (
            f"{db_name} schema landscape: {len(sources)} sources, "
            f"{len(topics)} topics, {len(table_groups)} table groups"
        ),
        "detail": detail,
        "source_collection_count": len(sources),
        "topic_family_count": len(topics),
        "table_group_count": len(table_groups),
        "table_count": int(counts.get("table_count") or 0),
        "column_count": int(counts.get("column_count") or 0),
        "grouped_table_count": total_grouped_tables,
        "schema_reading_strategy": (
            "Read this landscape first, then inspect schema/topic and table_group nodes before expanding physical tables."
        ),
        "labels": ["schema_landscape", "knowledge"],
        "project": project,
    }


def _markdown_detail(
    *,
    db_name: str,
    counts: dict,
    sources: list[str],
    topics: list[str],
    table_groups: list[str],
    omitted_sources: int,
    omitted_topics: int,
    omitted_table_groups: int,
) -> str:
    lines = [
        f"# Schema Landscape: {db_name}",
        "",
        "## How to read this database",
        "",
        "Start from source collections, then business/source topics, then physical table groups. Expand individual tables only after the question identifies a topic, date/cycle/version suffix, or variable column.",
        "",
        "## Counts",
        "",
        f"- Tables: {int(counts.get('table_count') or 0)}",
        f"- Columns: {int(counts.get('column_count') or 0)}",
        "",
        "## Source Collections",
        "",
    ]
    lines.extend(sources or ["- No source_collection nodes found."])
    if omitted_sources:
        lines.append(f"- ... {omitted_sources} more source collections omitted.")
    lines.extend(["", "## Semantic Topics", ""])
    lines.extend(topics or ["- No semantic topic nodes found."])
    if omitted_topics:
        lines.append(f"- ... {omitted_topics} more semantic topics omitted.")
    lines.extend(["", "## Physical Table Groups", ""])
    lines.extend(table_groups or ["- No table_group nodes found."])
    if omitted_table_groups:
        lines.append(f"- ... {omitted_table_groups} more table groups omitted.")
    lines.extend([
        "",
        "## Prompt Compression",
        "",
        "- For `same_order` and `same_set` table groups, read representative members instead of all members.",
        "- For `drifting` table groups, use common columns as the stable schema and inspect representative members for variable columns.",
        "- For compact year/cycle suffix families such as `INDIVYY` or `PAS2YY`, decode the suffix before choosing physical members.",
    ])
    return "\n".join(lines).strip() + "\n"


def _source_line(item: dict) -> str:
    topics = _string_list(item.get("dominant_topics"))[:6]
    topic_text = ", ".join(topics) if topics else "none"
    return (
        f"- `{item.get('schema_name') or item.get('topic_key')}`: "
        f"{int(item.get('physical_table_count') or 0)} physical tables, "
        f"{int(item.get('logical_unit_count') or 0)} logical units, "
        f"dominant topics: {topic_text}."
    )


def _topic_line(item: dict) -> str:
    units = _logical_unit_names(item.get("logical_units"))[:5]
    unit_text = ", ".join(units) if units else "none"
    topic_name = item.get("topic_key") or item.get("topic_label") or item.get("name")
    return (
        f"- `{item.get('schema_name')}.{topic_name}`: "
        f"{int(item.get('physical_table_count') or 0)} physical tables, "
        f"{int(item.get('logical_unit_count') or 0)} logical units, "
        f"{int(item.get('table_group_count') or 0)} table groups; "
        f"units: {unit_text}."
    )


def _table_group_line(item: dict) -> str:
    reps = _string_list(item.get("representative_members"))[:3]
    rep_text = ", ".join(reps) if reps else str(item.get("representative_member") or "none")
    return (
        f"- `{item.get('schema_name')}.{item.get('family')}`: "
        f"{int(item.get('member_count') or 0)} members, "
        f"{item.get('primary_pattern_type') or 'pattern'} / {item.get('consistency') or 'unknown'}, "
        f"common_cols={int(item.get('common_column_count') or 0)}, "
        f"union_cols={int(item.get('union_column_count') or 0)}, "
        f"representatives: {rep_text}."
    )


def _string_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item is not None]
    return []


def _logical_unit_names(value) -> list[str]:
    if not isinstance(value, str):
        if isinstance(value, list):
            return [str(item.get("name") or item) if isinstance(item, dict) else str(item) for item in value]
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item.get("name") or "") for item in parsed if isinstance(item, dict) and item.get("name")]


def _linked_refs(*node_lists: list[dict]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for nodes in node_lists:
        for node in nodes:
            ref = str(node.get("_ref") or node.get("name") or "")
            if ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    return refs


def _upsert_landscape(store, *, project: str, db_ref: str, summary: dict, linked_refs: list[str]) -> None:
    props = neo4j_props({key: value for key, value in summary.items() if key != "labels"})
    store.execute_cypher(
        """
        MATCH (d:db {_ref: $db_ref, project: $project})
        MERGE (l:schema_landscape:knowledge {_ref: $ref})
        ON CREATE SET l.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8)
        ON MATCH SET l.id = coalesce(l.id, 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8))
        SET l += $props
        SET l.project = $project
        SET l.labels = ['schema_landscape', 'knowledge']
        MERGE (d)-[:RELATED_TO]->(l)
        """,
        params={"db_ref": db_ref, "project": project, "ref": summary["_ref"], "props": props},
    )
    if linked_refs:
        store.execute_cypher(
            """
            MATCH (l:schema_landscape {_ref: $ref, project: $project})
            UNWIND $refs AS linked_ref
            MATCH (n {project: $project})
            WHERE n._ref = linked_ref OR n.name = linked_ref
            MERGE (n)-[:RELATED_TO]->(l)
            """,
            params={"project": project, "ref": summary["_ref"], "refs": linked_refs},
        )
