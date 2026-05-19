"""Store module protocol.

This is the only storage-internal module that source modules should import.
Concrete modules receive a `ModuleContext`; they must not inspect `Store`
internals or import peer source modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from storage.query_inspector import cypher_label_clause

_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

@dataclass
class ResolverPointer:
    project: str
    module: str
    kind: str
    payload: str


@dataclass
class CypherStatement:
    query: str
    params: dict = field(default_factory=dict)


def make_pointer(module: str, kind: str, payload: str, *, project: str = "") -> str:
    """Build a Pontis resolver pointer string.

    The payload is module-owned. Keep it free of literal ">" unless the module
    encodes it first.
    """
    if not project:
        raise ValueError("resolver pointer requires a project name")
    return f"<pontis:{project}:{module}:{kind}:{payload}>"


def parse_pointer(value: str) -> ResolverPointer | None:
    """Parse a complete resolver pointer string."""
    if not isinstance(value, str):
        return None
    prefix = "<pontis:"
    if not value.startswith(prefix) or not value.endswith(">"):
        return None
    body = value[len(prefix):-1]
    parts = body.split(":", 3)
    if len(parts) != 4:
        return None
    project, module, kind, payload = parts
    if project and not _TOKEN_RE.match(project):
        return None
    if not _TOKEN_RE.match(module) or not _TOKEN_RE.match(kind):
        return None
    return ResolverPointer(
        project=project,
        module=module,
        kind=kind,
        payload=payload,
    )


@dataclass
class ModuleContext:
    project_name: str
    project_config: Any
    source_config: Any
    graph_config: Any
    source: Any
    cache: dict = field(default_factory=dict)


class StoreModule:
    """Base protocol for source modules.

    Active query-time flow:

    ```text
    Workspace.cypher(...)
      -> TriggerRouter calls module.wants(event)
      -> selected module returns CypherStatement objects
      -> Store executes those statements
      -> Neo4j runs the original query
      -> Workspace resolves returned pointer strings
    ```

    A module should be flat and self-contained: it receives only
    `ModuleContext`, reads data through `ctx.source`, and does not import peer
    modules or Store internals.
    """
    name = "module"
    refresh_interval_seconds = 300.0

    def __init__(self, ctx: ModuleContext | None = None):
        self.ctx = ctx

    @property
    def project_name(self) -> str:
        return self.ctx.project_name if self.ctx else ""

    def pointer(self, kind: str, payload: str) -> str:
        """Build a project-aware resolver pointer for this module."""
        return make_pointer(self.name, kind, payload, project=self.project_name)

    def wants(self, event) -> bool:
        """Return whether this module should run for a trigger event."""
        if getattr(event, "type", "") not in {"query", "write", "refresh"}:
            return False
        return self.should_materialize_for_query(
            getattr(event, "parsed_query", None),
            getattr(event, "query", ""),
        )

    def should_materialize_for_query(self, parsed, raw_query: str = "") -> bool:
        """Return whether this module should publish its virtual subgraph.

        Workspace must not know source-specific labels or type rules. Each
        module decides whether a query touches the entity types or access ports
        it owns.
        """
        return False

    def cypher_statements(self) -> list[CypherStatement]:
        """Return write statements used to refresh this module's facts.

        Store does not interpret a module's identity model. The module owns its
        Cypher MERGE / MATCH / DELETE semantics and Store only executes the
        returned statements in order.
        """
        return []

    def source_fingerprint(self) -> str | None:
        """Return a cheap source-state fingerprint for refresh skipping.

        Store uses this only after `refresh_interval_seconds` has elapsed. A
        module may return None to rely on TTL-only freshness.
        """
        return None

    def resolve_pointer(self, kind: str, payload: str, *, node: dict | None = None):
        """Resolve a returned `<pontis:project:module:kind:payload>` string.

        Neo4j only sees the pointer as a normal string. Workspace calls this
        after Neo4j has returned rows.
        """
        return None
