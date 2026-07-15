"""Query tool prompt — SQL 执行工具。"""

DESCRIPTION = "Run read-only SQL on a database-source ref, CSV/TSV/JSON ref, or current workspace."

DETAIL = """\
Required: `sql`, `ref`. Optional: `limit` (default 20, minimum 1).
SQL must be read-only: SELECT / WITH SELECT / read-only PRAGMA.
DB refs use native table names. CSV/TSV/JSON records expose table `this` and usually a filename alias. `ref="."` registers all structured sources in the workspace.
Quote special column names with double quotes.
For a source-rooted database descendant ref (db/table/col/fk/rel/disambig/knowledge),
query selects the owning database at the `:db` segment. It does not restrict SQL to
the terminal entity or table; SQL may access any native table in that database.
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
