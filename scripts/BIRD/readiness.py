"""Fail-fast readiness checks shared by BIRD extraction and benchmark runs."""

from __future__ import annotations

from dataclasses import dataclass

from explorer.readme import README_MAX_CHARS
from extractor.semantic_embedding import pending_embedding_nodes
from storage.workspace import Workspace


@dataclass(frozen=True)
class BirdGraphReadiness:
    database_nodes: int
    tables: int
    columns: int
    missing_descriptions: int
    missing_official_metadata: int
    readmes: int
    oversized_readmes: int
    pending_column_domains: int
    stale_embeddings: int

    @property
    def issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if self.database_nodes != 1:
            issues.append(f"expected exactly one db source, found {self.database_nodes}")
        if self.tables < 1:
            issues.append("no table entities")
        if self.columns < 1:
            issues.append("no column entities")
        if self.missing_descriptions:
            issues.append(f"{self.missing_descriptions} table/column entities lack brief or detail")
        if self.missing_official_metadata:
            issues.append(f"{self.missing_official_metadata} columns lack imported official metadata")
        if self.readmes != 1:
            issues.append(f"expected exactly one non-empty README, found {self.readmes}")
        if self.oversized_readmes:
            issues.append(
                f"{self.oversized_readmes} README exceeds the {README_MAX_CHARS}-character limit"
            )
        if self.pending_column_domains:
            issues.append(f"{self.pending_column_domains} column domains are still pending review")
        if self.stale_embeddings:
            issues.append(f"{self.stale_embeddings} searchable nodes have missing or stale embeddings")
        return tuple(issues)


def inspect_bird_graph(workspace: Workspace, *, config=None) -> BirdGraphReadiness:
    counts = workspace.cypher(
        """
        MATCH (n)
        RETURN
          sum(CASE WHEN n:db THEN 1 ELSE 0 END) AS database_nodes,
          sum(CASE WHEN n:table THEN 1 ELSE 0 END) AS tables,
          sum(CASE WHEN n:col THEN 1 ELSE 0 END) AS columns,
          sum(CASE WHEN (n:table OR n:col) AND
                        (n.brief IS NULL OR trim(toString(n.brief)) = '' OR
                         n.detail IS NULL OR trim(toString(n.detail)) = '')
                   THEN 1 ELSE 0 END) AS missing_descriptions,
          sum(CASE WHEN n:col AND
                        n.official_column_description IS NULL AND
                        n.official_value_description IS NULL
                   THEN 1 ELSE 0 END) AS missing_official_metadata,
          sum(CASE WHEN n:knowledge AND n.name = 'README' AND
                        n.detail IS NOT NULL AND trim(toString(n.detail)) <> ''
                   THEN 1 ELSE 0 END) AS readmes,
          sum(CASE WHEN n:knowledge AND n.name = 'README' AND
                        size(toString(coalesce(n.detail, ''))) > $readme_max_chars
                   THEN 1 ELSE 0 END) AS oversized_readmes,
          sum(CASE WHEN n:column_domain AND
                        coalesce(n.review_status, 'pending_review') = 'pending_review'
                   THEN 1 ELSE 0 END) AS pending_column_domains
        """,
        params={"readme_max_chars": README_MAX_CHARS},
    )
    row = counts[0] if counts else {}
    stale_embeddings = len(pending_embedding_nodes(workspace, config=config))
    return BirdGraphReadiness(
        database_nodes=int(row.get("database_nodes") or 0),
        tables=int(row.get("tables") or 0),
        columns=int(row.get("columns") or 0),
        missing_descriptions=int(row.get("missing_descriptions") or 0),
        missing_official_metadata=int(row.get("missing_official_metadata") or 0),
        readmes=int(row.get("readmes") or 0),
        oversized_readmes=int(row.get("oversized_readmes") or 0),
        pending_column_domains=int(row.get("pending_column_domains") or 0),
        stale_embeddings=stale_embeddings,
    )


def assert_bird_graph_ready(workspace: Workspace, *, config=None) -> BirdGraphReadiness:
    readiness = inspect_bird_graph(workspace, config=config)
    if readiness.issues:
        project = ", ".join(workspace.active_projects) or workspace.project_path or "unknown"
        details = "; ".join(readiness.issues)
        raise RuntimeError(f"BIRD graph is not ready for {project}: {details}")
    return readiness


def assert_bird_database_ready(db_dir: str) -> BirdGraphReadiness:
    from scripts.preprocess_engine import init_workspace

    workspace, config = init_workspace(db_dir)
    return assert_bird_graph_ready(workspace, config=config)
