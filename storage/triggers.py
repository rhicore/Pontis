"""Trigger routing for storage source modules."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class TriggerEvent:
    """Why a storage module is being considered for execution."""

    type: str
    project: str
    source_scope: str = ""
    query: str = ""
    parsed_query: Any = None
    reason: str = ""
    payload: dict = field(default_factory=dict)


class TriggerRouter:
    """Select modules for a trigger without source-specific branching."""

    def select(self, modules: list, event: TriggerEvent) -> list:
        selected = []
        for module in modules:
            try:
                wants = module.wants(event)
            except Exception:
                logger.exception("Source module %s failed trigger selection", module.name)
                wants = False
            if wants:
                selected.append(module)
        # The project anchor must exist before any public result is formatted
        # as a source-rooted ref. Store freshness keeps this inexpensive.
        for module in modules:
            try:
                owns_anchor = bool(module.provides_source_anchor())
            except Exception:
                owns_anchor = False
            if owns_anchor and module not in selected:
                selected.insert(0, module)
        return selected
