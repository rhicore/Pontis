"""Graph policy reconciliation and invariant checks.

Graph policies maintain derived labels and validate graph invariants after
storage writes. They are intentionally not source modules: source modules
materialize external facts, while policies keep the Pontis graph internally
consistent and easy to navigate through the simplified tool surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyViolation:
    rule: str
    severity: str
    message: str
    rows: list[dict]


class DerivedLabelRule(Protocol):
    name: str

    def apply(self, store, project: str) -> None:
        ...


class InvariantRule(Protocol):
    name: str
    severity: str

    def check(self, store, project: str) -> list[dict]:
        ...


class TableGroupStatusLabels:
    """Maintain :grouped/:standalone labels for tables covered by table groups."""

    name = "table_group_status_labels"

    def apply(self, store, project: str) -> None:
        _execute_many(
            store,
            [
                (
                    """
                    MATCH (t:table {project: $project})
                    WHERE EXISTS {
                        MATCH (t)--(g:table_group {project: $project})
                    }
                    SET t:grouped
                    REMOVE t:standalone
                    """,
                    {"project": project},
                ),
                (
                    """
                    MATCH (t:table {project: $project})
                    WHERE NOT EXISTS {
                        MATCH (t)--(g:table_group {project: $project})
                    }
                    SET t:standalone
                    REMOVE t:grouped
                    """,
                    {"project": project},
                ),
            ],
        )


class ColumnGroupStatusLabels:
    """Maintain labels for columns covered by structural or logical groups."""

    name = "column_group_status_labels"

    def apply(self, store, project: str) -> None:
        _execute_many(
            store,
            [
                (
                    """
                    MATCH (c:col {project: $project})
                    WHERE EXISTS {
                        MATCH (c)--(g {project: $project})
                        WHERE g:column_group OR g:logical_col
                    }
                    SET c:grouped
                    REMOVE c:standalone
                    """,
                    {"project": project},
                ),
                (
                    """
                    MATCH (c:col {project: $project})
                    WHERE NOT EXISTS {
                        MATCH (c)--(g {project: $project})
                        WHERE g:column_group OR g:logical_col
                    }
                    SET c:standalone
                    REMOVE c:grouped
                    """,
                    {"project": project},
                ),
            ],
        )


class TableGroupedStandaloneXor:
    name = "table_grouped_standalone_xor"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (t:table:grouped:standalone {project: $project})
            RETURN coalesce(t._ref, t.path, t.name) AS table_ref
            LIMIT 100
            """,
            params={"project": project},
        )


class ColumnGroupedStandaloneXor:
    name = "column_grouped_standalone_xor"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (c:col:grouped:standalone {project: $project})
            RETURN coalesce(c._ref, c.path, c.name) AS col_ref
            LIMIT 100
            """,
            params={"project": project},
        )


class ColumnSingleStructuralParent:
    name = "column_single_structural_parent"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (c:col {project: $project})
            OPTIONAL MATCH (c)--(parent {project: $project})
            WHERE parent:table OR parent:view
            WITH c, collect(DISTINCT coalesce(parent._ref, parent.path, parent.name)) AS parents
            WHERE size(parents) <> 1
            RETURN coalesce(c._ref, c.path, c.name) AS col_ref, parents
            LIMIT 100
            """,
            params={"project": project},
        )


class ColumnGroupSingleStructuralParent:
    name = "column_group_single_structural_parent"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (g:column_group {project: $project})
            OPTIONAL MATCH (g)--(parent {project: $project})
            WHERE parent:table OR parent:view
            WITH g, collect(DISTINCT coalesce(parent._ref, parent.path, parent.name)) AS parents
            WHERE size(parents) <> 1
            RETURN coalesce(g._ref, g.path, g.name) AS column_group_ref, parents
            LIMIT 100
            """,
            params={"project": project},
        )


class ColumnGroupMembersShareParent:
    name = "column_group_members_share_parent"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (g:column_group {project: $project})
            OPTIONAL MATCH (g)--(group_parent {project: $project})
            WHERE group_parent:table OR group_parent:view
            WITH g, collect(DISTINCT coalesce(group_parent._ref, group_parent.path, group_parent.name)) AS group_parents
            MATCH (g)--(c:col {project: $project})
            OPTIONAL MATCH (c)--(col_parent {project: $project})
            WHERE col_parent:table OR col_parent:view
            WITH g, group_parents,
                 collect(DISTINCT coalesce(col_parent._ref, col_parent.path, col_parent.name)) AS col_parents
            WHERE size(group_parents) <> 1
               OR size(col_parents) <> 1
               OR group_parents[0] <> col_parents[0]
            RETURN coalesce(g._ref, g.path, g.name) AS column_group_ref,
                   group_parents,
                   col_parents
            LIMIT 100
            """,
            params={"project": project},
        )


class LogicalColumnSingleTableGroupParent:
    name = "logical_column_single_table_group_parent"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (l:logical_col {project: $project})
            OPTIONAL MATCH (l)--(g:table_group {project: $project})
            WITH l, collect(DISTINCT coalesce(g._ref, g.name)) AS groups
            WHERE size(groups) <> 1
            RETURN coalesce(l._ref, l.name) AS logical_col_ref, groups
            LIMIT 100
            """,
            params={"project": project},
        )


class ColumnSingleLogicalColumn:
    name = "column_single_logical_column"
    severity = "hard"

    def check(self, store, project: str) -> list[dict]:
        return store.execute_cypher(
            """
            MATCH (c:col {project: $project})
            OPTIONAL MATCH (c)--(l:logical_col {project: $project})
            WITH c, collect(DISTINCT coalesce(l._ref, l.name)) AS logical_columns
            WHERE size(logical_columns) > 1
            RETURN coalesce(c._ref, c.path, c.name) AS col_ref, logical_columns
            LIMIT 100
            """,
            params={"project": project},
        )


class GraphPolicyError(RuntimeError):
    """Raised when hard graph invariants are violated."""

    def __init__(self, violations: list[PolicyViolation]):
        self.violations = violations
        lines = ["Graph policy hard invariant violation:"]
        for violation in violations:
            lines.append(f"- {violation.rule}: {violation.message}")
        super().__init__("\n".join(lines))


class GraphPolicyEngine:
    """Apply derived labels and validate graph invariants."""

    def __init__(
        self,
        derived_rules: list[DerivedLabelRule] | None = None,
        invariant_rules: list[InvariantRule] | None = None,
    ):
        self.derived_rules = list(derived_rules or DEFAULT_DERIVED_RULES)
        self.invariant_rules = list(invariant_rules or DEFAULT_INVARIANT_RULES)

    def reconcile(
        self,
        store,
        *,
        mode: str = "light",
        raise_on_hard: bool = False,
    ) -> list[PolicyViolation]:
        project = getattr(store, "project_name", "") or ""
        if not project:
            return []

        for rule in self.derived_rules:
            try:
                rule.apply(store, project)
            except Exception:
                logger.exception("Graph policy derived rule failed: %s", rule.name)
                raise

        if mode != "full":
            return []

        violations = self.validate(store)
        hard = [v for v in violations if v.severity == "hard"]
        if hard and raise_on_hard:
            raise GraphPolicyError(hard)
        return violations

    def validate(self, store) -> list[PolicyViolation]:
        project = getattr(store, "project_name", "") or ""
        if not project:
            return []
        violations: list[PolicyViolation] = []
        for rule in self.invariant_rules:
            rows = rule.check(store, project)
            if not rows:
                continue
            violations.append(
                PolicyViolation(
                    rule=rule.name,
                    severity=rule.severity,
                    message=f"{len(rows)} sampled violation rows",
                    rows=rows,
                )
            )
        return violations


def _execute_many(store, statements: list[tuple[str, dict]]) -> None:
    for query, params in statements:
        store.execute_cypher(query, params=params)


DEFAULT_DERIVED_RULES: list[DerivedLabelRule] = [
    TableGroupStatusLabels(),
    ColumnGroupStatusLabels(),
]

DEFAULT_INVARIANT_RULES: list[InvariantRule] = [
    TableGroupedStandaloneXor(),
    ColumnGroupedStandaloneXor(),
    ColumnSingleStructuralParent(),
    ColumnGroupSingleStructuralParent(),
    ColumnGroupMembersShareParent(),
    LogicalColumnSingleTableGroupParent(),
    ColumnSingleLogicalColumn(),
]


DEFAULT_GRAPH_POLICY_ENGINE = GraphPolicyEngine()
