"""Query tool prompt — SQL 执行工具。"""

DESCRIPTION = "Run read-only SQL on a DB/CSV/TSV/JSON ref or current workspace."

DETAIL = """\
Required: `sql`, `ref`. Optional: `limit` (default 20).
SQL must be read-only: SELECT / WITH SELECT / read-only PRAGMA.
DB refs use native table names. CSV/TSV/JSON records expose table `this` and usually a filename alias. `ref="."` registers all structured sources in the workspace.
Quote special column names with double quotes.
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
