"""Unified graph lifecycle for derived column-domain candidates."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from extractor.utils.graph_write import refreshable_metadata, write_project_cypher
from extractor.utils.refs import neo4j_props
from storage.workspace import Workspace


ENTITY_METHOD = "column_domain_candidate_v1"


def sync_column_domains(
    workspace: Workspace,
    db_ref: str,
    candidates: list[dict],
) -> int:
    """Upsert one strategy's complete candidate set for a database."""

    active_refs: list[str] = []
    member_links: list[dict] = []
    created = 0
    for candidate in candidates:
        member_refs = sorted({str(ref) for ref in candidate["member_refs"] if ref})
        if len(member_refs) < 2:
            continue
        ref = column_domain_ref(db_ref, member_refs)
        active_refs.append(ref)
        if _upsert_domain(
            workspace,
            ref=ref,
            name=column_domain_name(ref),
            metadata={
                **candidate.get("metadata", {}),
                "entity_method": ENTITY_METHOD,
            },
        ):
            created += 1
        member_links.append({"domain_ref": ref, "member_refs": member_refs})

    _connect_members(workspace, member_links)
    _connect_database(workspace, db_ref, active_refs)

    _delete_stale_domains(workspace, db_ref, active_refs)
    _delete_legacy_candidates(workspace, db_ref)
    return created


def column_domain_ref(db_ref: str, member_refs: list[str]) -> str:
    digest = hashlib.sha1("|".join(sorted(member_refs)).encode("utf-8")).hexdigest()[:12]
    return f"{db_ref}--column_domain--{digest}"


def column_domain_name(ref: str) -> str:
    return f"column_domain_{ref.rsplit('--', 1)[-1]}"


def _upsert_domain(
    workspace: Workspace,
    *,
    ref: str,
    name: str,
    metadata: dict,
) -> bool:
    existing = bool(workspace.cypher(
        "MATCH (d:column_domain {_ref: $ref}) RETURN d LIMIT 1",
        params={"ref": ref},
    ))
    props = neo4j_props({
        "_ref": ref,
        "name": name,
        "labels": ["column_domain", "domain"],
        "review_status": metadata.get("review_status") or "pending_review",
        **metadata,
    })
    write_project_cypher(
        workspace,
        """
        MERGE (d:column_domain:domain {_ref: $ref, project: $project})
        ON CREATE SET d.id = 'ent_' + substring(replace(randomUUID(), '-', ''), 0, 8),
                      d.created_at = $created_at,
                      d += $props
        ON MATCH SET d += $refresh_props
        SET d.labels = reduce(acc = [], label IN coalesce(d.labels, []) + ['column_domain', 'domain'] |
            CASE WHEN label IN acc THEN acc ELSE acc + label END)
        """,
        params={
            "ref": ref,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "props": props,
            "refresh_props": refreshable_metadata(props),
        },
    )
    return not existing


def _connect_members(workspace: Workspace, links: list[dict]) -> None:
    # Keep the two labels in separate indexed MATCH clauses.  The historical
    # label/property OR query forced a project-wide node scan for every member.
    for label in ("col", "logical_col"):
        write_project_cypher(
            workspace,
            f"""
            UNWIND $links AS link
            MATCH (d:column_domain {{_ref: link.domain_ref, project: $project}})
            UNWIND link.member_refs AS member_ref
            MATCH (c:{label} {{_ref: member_ref, project: $project}})
            MERGE (c)-[:RELATED_TO]->(d)
            """,
            params={"links": links},
        )


def _connect_database(workspace: Workspace, db_ref: str, refs: list[str]) -> None:
    write_project_cypher(
        workspace,
        """
        MATCH (db:db {project: $project})
        WHERE db._ref = $db_ref OR db.path = $db_ref OR db.name = $db_ref
        UNWIND $refs AS ref
        MATCH (d:column_domain {_ref: ref, project: $project})
        MERGE (db)-[:RELATED_TO]->(d)
        """,
        params={"db_ref": db_ref, "refs": refs},
    )


def _delete_stale_domains(workspace: Workspace, db_ref: str, active_refs: list[str]) -> None:
    write_project_cypher(
        workspace,
        """
        MATCH (db:db {project: $project})
        WHERE db._ref = $db_ref OR db.path = $db_ref OR db.name = $db_ref
        MATCH (db)-[:RELATED_TO*0..3]-(d:column_domain)
        WHERE d.entity_method = $entity_method AND NOT d._ref IN $active_refs
        DETACH DELETE d
        """,
        params={"db_ref": db_ref, "entity_method": ENTITY_METHOD, "active_refs": active_refs},
    )


def _delete_legacy_candidates(workspace: Workspace, db_ref: str) -> None:
    """Remove superseded extractor candidates after their unified replacements exist."""

    write_project_cypher(
        workspace,
        """
        MATCH (db:db {project: $project})
        WHERE db._ref = $db_ref OR db.path = $db_ref OR db.name = $db_ref
        MATCH (db)-[:RELATED_TO*0..3]-(old)
        WHERE (old:overlap OR old:value_domain)
          AND NOT old:column_domain
        DETACH DELETE old
        """,
        params={"db_ref": db_ref},
    )
