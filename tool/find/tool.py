"""Find tool — unified graph entity discovery.

`find` is the public tool-facing wrapper for graph entity discovery:
- ref-only lookup lists entities by graph ref.
- ref + query performs semantic/BM25 lookup scoped by ref.
"""

from __future__ import annotations

from typing import Optional

from tool.config import TOOL_PAGINATION


def find_command(
    workspace,
    ref: str = "",
    query: str = "",
    offset: int = 0,
    limit: Optional[int] = None,
    current_cwd: str = "",
) -> str:
    """Find graph entities by ref pattern, semantic query, or both.

    Args:
        workspace: Workspace instance.
        ref: Graph ref pattern used to constrain candidates.
        query: Natural language query used for semantic/BM25 search.
        offset: Starting index.
        limit: Max rows to return.
    """
    ref = (ref or "").strip()
    query = (query or "").strip()

    if not ref:
        return 'Error: find requires ref, e.g. find({"ref":"*:file"})'

    if not query:
        from tool.utils.ref_match import match_ref_command

        return match_ref_command(
            workspace,
            ref=ref,
            offset=offset,
            limit=limit,
            current_cwd=current_cwd,
        )

    from tool.utils.entity_search import search_entities_command

    if limit is None:
        limit = TOOL_PAGINATION["find"].default_limit
    return search_entities_command(
        workspace,
        ref=ref,
        query=query,
        offset=offset,
        limit=limit,
        current_cwd=current_cwd,
    )


if __name__ == "__main__":
    import json
    import sys
    from storage.workspace import Workspace

    if len(sys.argv) < 3:
        print("Usage: python -m tool.find.tool <project_name> <json_params>")
        sys.exit(1)

    ws = Workspace(sys.argv[1])
    _params = json.loads(sys.argv[2])
    print(find_command(ws, **_params))
