"""Shared utilities for Pontis tool layer."""

import os


def execute_cypher(obj, cypher: str):
    """Execute a Cypher query via obj.query() or fall back to CypherExecutor."""
    if hasattr(obj, 'query'):
        return obj.query(cypher)
    from storage.cypher import parse_cypher, CypherExecutor
    store = obj if not hasattr(obj, 'get_store') else obj.get_store()
    executor = CypherExecutor(store)
    return executor.execute(parse_cypher(cypher))


def get_store(obj):
    """Extract the Store from a Workspace or return obj directly if it is a Store."""
    if hasattr(obj, 'get_store'):
        return obj.get_store()
    return obj


def get_project_name(obj) -> str:
    """Derive the project name from a Workspace or Store instance."""
    if hasattr(obj, 'config'):
        dp = obj.config.default_project()
        if dp:
            return dp
    if hasattr(obj, 'project_path'):
        return os.path.basename(obj.project_path)
    return "local"


def cypher_escape(v) -> str:
    """Escape a value for safe interpolation into a Cypher string literal."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')
