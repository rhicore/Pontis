"""Shared utilities for Pontis tool layer."""


def execute_cypher(workspace, cypher: str, params: dict = None, project: str = None):
    """Execute a Cypher query via workspace.cypher()."""
    return workspace.cypher(cypher, params=params, project=project)


def get_project_name(workspace) -> str:
    """Derive the project name from a Workspace."""
    ap = workspace.active_projects
    if ap:
        return ap[0]
    return "local"


def cypher_escape(v) -> str:
    """Escape a value for safe interpolation into a Cypher string literal."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')
