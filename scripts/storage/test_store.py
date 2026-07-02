"""Neo4j storage integration tests.

Prerequisites:
  source .neo4j/neo4j.env
  uv run python -m scripts.neo4j_instances start storage_test

The tests exercise the current storage architecture:
- Workspace.cypher selects source modules through query triggers.
- Source modules submit their own Cypher writes.
- Neo4j executes the final user query.
- Workspace resolves returned pointer strings.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.config import GraphConfig, ProjectConfig, SourceConfig, StoreConfig, load_config
from storage.stores.access import DbConnect, FileOpen
from storage.stores.utils.fs_adapter import LocalSourceAdapter
from storage.triggers import TriggerRouter
from storage.workspace import Workspace

_ALLOW_SHARED_TEST_DB = os.environ.get("PONTIS_ALLOW_SHARED_NEO4J_STORAGE_TEST") == "1"
_TEST_PROJECT = os.environ.get("PONTIS_STORAGE_TEST_PROJECT", "storage_test")


def _graph_config() -> GraphConfig:
    project = load_config().projects.get(_TEST_PROJECT)
    if project:
        return project.graph
    return GraphConfig(
        uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", ""),
    )


def _is_shared_default_graph(graph) -> bool:
    uri = (getattr(graph, "uri", "") or "").rstrip("/")
    database = getattr(graph, "database", "") or "neo4j"
    return database == "neo4j" and uri in {"", "bolt://localhost:7687", "bolt://127.0.0.1:7687"}


def _build_workspace(project_roots: dict[str, Path]) -> Workspace:
    ws = Workspace.__new__(Workspace)
    ws._config = StoreConfig(
        projects={
            name: ProjectConfig(
                name=name,
                source=SourceConfig(type="fs", path=str(root)),
                graph=_graph_config(),
            )
            for name, root in project_roots.items()
        }
    )
    ws._stores = {}
    ws._modules = {}
    ws._trigger_router = TriggerRouter()
    for name in project_roots:
        ws._register_project(name)
    return ws


def _clear_graph(ws: Workspace, batch_size: int = 1000):
    for store in ws._stores.values():
        graph = getattr(store, "_graph", None)
        database = getattr(graph, "database", "") or "neo4j"
        if _is_shared_default_graph(graph) and not _ALLOW_SHARED_TEST_DB:
            raise RuntimeError(
                "Refusing to clear shared default Neo4j graph. "
                "Run storage tests against pontis.yml project 'storage_test', "
                "or explicitly set "
                "PONTIS_ALLOW_SHARED_NEO4J_STORAGE_TEST=1."
            )
        while True:
            rows = store.execute_cypher(
                "MATCH (n) WITH n LIMIT $limit "
                "DETACH DELETE n "
                "RETURN count(n) AS deleted",
                params={"limit": batch_size},
            )
            deleted = rows[0]["deleted"] if rows else 0
            if deleted == 0:
                break
        store.invalidate_modules()
        return


def _assert_equal(actual, expected, msg: str = ""):
    assert actual == expected, f"{msg}\nexpected={expected!r}\nactual={actual!r}"


def _assert_true(value, msg: str):
    assert value, msg


def _create_sqlite(path: Path, schema_sql: str):
    conn = sqlite3.connect(path)
    conn.executescript(schema_sql)
    conn.close()


def _prepare_alpha(root: Path):
    (root / "docs").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "archive").mkdir(parents=True)
    (root / "README.md").write_text("alpha\nreadme\n", encoding="utf-8")
    (root / "docs" / "README.md").write_text("nested\nreadme\n", encoding="utf-8")
    (root / "docs" / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "data" / "people.csv").write_text(
        "id,name,score\n1,Alice,9.5\n2,Bob,8.0\n",
        encoding="utf-8",
    )
    (root / "archive" / "people.csv").write_text(
        "id,name,score\n3,Carol,7.5\n",
        encoding="utf-8",
    )
    (root / "data" / "events.tsv").write_text(
        "event_id\tname\n10\tlogin\n",
        encoding="utf-8",
    )
    (root / "payload.parquet").write_text("not really parquet\n", encoding="utf-8")

    _create_sqlite(
        root / "app.sqlite",
        """
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books (id INTEGER PRIMARY KEY, author_id INTEGER, title TEXT);
        CREATE VIEW user_names AS SELECT name FROM users;
        INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO orders VALUES (10, 1, 12.5), (11, 2, 8.0);
        INSERT INTO authors VALUES (1, 'Ann');
        INSERT INTO books VALUES (100, 1, 'Graph Systems');
        """,
    )


def _prepare_beta(root: Path):
    (root / "README.md").write_text("beta\nreadme\n", encoding="utf-8")
    (root / "data.csv").write_text("id,value\n1,blue\n", encoding="utf-8")
    _create_sqlite(
        root / "app.sqlite",
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, handle TEXT);
        INSERT INTO users VALUES (1, 'beta_user');
        """,
    )


def test_single_project_complex(ws: Workspace):
    project = "alpha"

    files = ws.cypher(
        "MATCH (f:file) RETURN f.path AS path, labels(f) AS labels ORDER BY path",
        project=project,
    )
    paths = {row["path"] for row in files}
    _assert_equal(
        paths,
        {
            "README.md",
            "app.sqlite",
            "archive/people.csv",
            "data/events.tsv",
            "data/people.csv",
            "docs/README.md",
            "docs/notes.txt",
            "payload.parquet",
        },
        "FS module should publish nested files and suffix labels.",
    )
    parquet = next(row for row in files if row["path"] == "payload.parquet")
    _assert_true("parquet" in parquet["labels"], "FS should derive arbitrary suffix labels dynamically.")
    sqlite_labels = next(row for row in files if row["path"] == "app.sqlite")["labels"]
    _assert_true("sqlite" in sqlite_labels, "FS should label .sqlite by suffix.")

    csv_cols = ws.cypher(
        "MATCH (c:col) WHERE c._ref STARTS WITH 'data/people.csv--' "
        "RETURN c.name AS name, c.col_type AS col_type ORDER BY c.ordinal",
        project=project,
    )
    _assert_equal(
        csv_cols,
        [
            {"name": "id", "col_type": "INT"},
            {"name": "name", "col_type": "TEXT"},
            {"name": "score", "col_type": "FLOAT"},
        ],
        "CSV schema module should infer nested CSV columns.",
    )

    csv_file = ws.cypher(
        "MATCH (f:csv:text {path: 'data/people.csv'}) "
        "RETURN f.line_count AS line_count, f.char_count AS char_count",
        project=project,
    )
    _assert_equal(len(csv_file), 1, "CSV and text modules should merge onto one file node.")
    _assert_equal(csv_file[0]["line_count"], 3, "Text module should add text metadata to CSV node.")

    db = ws.cypher(
        "MATCH (d:db {path: 'app.sqlite'}) "
        "RETURN d.table_count AS table_count, d.view_count AS view_count",
        project=project,
    )
    _assert_equal(db, [{"table_count": 4, "view_count": 1}], "DB schema module should merge db metadata onto sqlite file node.")

    tables = ws.cypher(
        "MATCH (t:table) RETURN t.name AS name, t.column_count AS columns ORDER BY name",
        project=project,
    )
    _assert_equal(
        tables,
        [
            {"name": "authors", "columns": 2},
            {"name": "books", "columns": 3},
            {"name": "orders", "columns": 3},
            {"name": "users", "columns": 2},
        ],
        "SQLite module should publish table schema without scanning table rows.",
    )

    explicit_fk = ws.cypher(
        "MATCH (from:col {_ref: 'app.sqlite--orders--user_id'})--"
        "(fk:fk)--(to:col {_ref: 'app.sqlite--users--user_id'}) "
        "RETURN fk.name AS name, fk.confidence AS confidence",
        project=project,
    )
    _assert_equal(
        explicit_fk,
        [{"name": "orders.user_id->users.user_id", "confidence": 1.0}],
        "Explicit FK should be merged by related column nodes.",
    )

    inferred_fk = ws.cypher(
        "MATCH (from:col {_ref: 'app.sqlite--books--author_id'})--"
        "(fk:fk)--(to:col {_ref: 'app.sqlite--authors--id'}) "
        "RETURN fk.name AS name, fk.confidence AS confidence",
        project=project,
    )
    _assert_equal(
        inferred_fk,
        [{"name": "books.author_id->authors.id", "confidence": 0.7}],
        "Inferred FK should also be expressed through related column nodes.",
    )

    view = ws.cypher(
        "MATCH (v:view {_ref: 'app.sqlite--user_names'})--"
        "(c:col {_ref: 'app.sqlite--user_names--name'}) "
        "RETURN v.name AS view_name, v.column_count AS columns, c.name AS col_name",
        project=project,
    )
    _assert_equal(
        view,
        [{"view_name": "user_names", "columns": 1, "col_name": "name"}],
        "SQLite views and their columns should be exposed and connected.",
    )


def test_idempotent_refresh(ws: Workspace):
    project = "alpha"

    before = ws.cypher(
        "MATCH (n) RETURN count(n) AS nodes",
        project=project,
    )[0]["nodes"]
    for _ in range(3):
        ws.cypher(
            "MATCH (t:table) RETURN t.name AS name",
            project=project,
        )
        ws.cypher(
            "MATCH (f:csv:text {path: 'data/people.csv'}) RETURN f.name AS name",
            project=project,
        )
    after = ws.cypher(
        "MATCH (n) RETURN count(n) AS nodes",
        project=project,
    )[0]["nodes"]
    _assert_equal(after, before, "Repeated trigger refreshes should not duplicate nodes.")

    rel_count = ws.cypher(
        "MATCH (:col {_ref: 'app.sqlite--orders--user_id'})-[r:RELATED_TO]-"
        "(:fk {name: 'orders.user_id->users.user_id'}) "
        "RETURN count(r) AS rels",
        project=project,
    )[0]["rels"]
    _assert_equal(rel_count, 1, "Repeated FK refreshes should not duplicate relationships.")

    duplicate_edges = ws.cypher(
        "MATCH (a)-[r:RELATED_TO]->(b) "
        "WITH coalesce(a.path, a._ref, a.ref) AS a_key, coalesce(b.path, b._ref, b.ref) AS b_key, count(r) AS rels "
        "WHERE rels > 1 "
        "RETURN a_key, b_key, rels ORDER BY a_key, b_key",
        project=project,
    )
    _assert_equal(duplicate_edges, [], "Repeated refreshes should not create duplicate RELATED_TO edges.")


def test_same_name_paths_and_label_merging(ws: Workspace):
    project = "alpha"

    readmes = ws.cypher(
        "MATCH (f:file:text {name: 'README.md'}) "
        "RETURN f.path AS path, f.line_count AS lines, f.labels AS labels ORDER BY path",
        project=project,
    )
    _assert_equal(
        [row["path"] for row in readmes],
        ["README.md", "docs/README.md"],
        "Same-name files in different directories should remain distinct by path.",
    )
    for row in readmes:
        _assert_equal(row["lines"], 2, "Each same-name README should keep its own text metadata.")
        _assert_equal(
            row["labels"].count("file"),
            1,
            "Label merging should not duplicate labels after multiple modules refresh the same file.",
        )
        _assert_equal(
            row["labels"].count("text"),
            1,
            "Text labels should be merged once even when text queries run repeatedly.",
        )

    csv_columns = ws.cypher(
        "MATCH (c:col {name: 'score'}) "
        "WHERE c._ref ENDS WITH '--score' "
        "RETURN c._ref AS ref, c.col_type AS col_type ORDER BY ref",
        project=project,
    )
    _assert_equal(
        csv_columns,
        [
            {"ref": "archive/people.csv--score", "col_type": "FLOAT"},
            {"ref": "data/people.csv--score", "col_type": "FLOAT"},
        ],
        "Same-name CSV files in different directories should expose separate column nodes.",
    )

    csv_files = ws.cypher(
        "MATCH (f:csv:text {name: 'people.csv'}) "
        "RETURN f.path AS path, f.line_count AS lines ORDER BY path",
        project=project,
    )
    _assert_equal(
        csv_files,
        [
            {"path": "archive/people.csv", "lines": 2},
            {"path": "data/people.csv", "lines": 3},
        ],
        "Text and CSV metadata should merge onto the correct same-name file node.",
    )


def test_query_trigger_order_and_pointer_resolution(ws: Workspace):
    project = "alpha"

    fresh = _build_workspace({"alpha": Path(ws._stores[project].project_path)})
    _clear_graph(fresh)

    csv_col = fresh.cypher(
        "MATCH (c:col {_ref: 'data/people.csv--score'}) "
        "RETURN c.name AS name, c.source_column AS source_column",
        project=project,
    )
    _assert_equal(len(csv_col), 1, "A first query for :col should trigger CSV schema materialization.")
    _assert_equal(csv_col[0]["source_column"], "score", "CSV column should preserve the source column.")

    text_after_col = fresh.cypher(
        "MATCH (t:text {path: 'data/people.csv'}) "
        "RETURN t.line_count AS lines, t._file_open AS open_file",
        project=project,
    )
    _assert_equal(len(text_after_col), 1, "A later :text query should add text metadata to an existing CSV file node.")
    _assert_equal(text_after_col[0]["lines"], 3, "Text refresh after CSV refresh should keep correct line count.")
    _assert_true(isinstance(text_after_col[0]["open_file"], FileOpen), "Text file should reuse file_open after mixed trigger order.")

    db_col = fresh.cypher(
        "MATCH (c:col {_ref: 'app.sqlite--orders--amount'}) "
        "RETURN c._db_connect AS connect, c.name AS name, c.table_name AS table_name, c.column_name AS column_name",
        project=project,
    )
    _assert_equal(len(db_col), 1, "DB columns should materialize on a :col query even after CSV columns exist.")
    _assert_true(isinstance(db_col[0]["connect"], DbConnect), "DB column should expose db_connect.")
    _assert_equal(db_col[0]["table_name"], "orders", "DB column should preserve table name.")
    _assert_equal(db_col[0]["column_name"], "amount", "DB column should preserve column name.")

    db_connect = fresh.cypher(
        "MATCH (d:db {path: 'app.sqlite'}) RETURN d._db_connect AS connect",
        project=project,
    )[0]["connect"]
    _assert_true(isinstance(db_connect, DbConnect), "db_connect pointer should resolve.")
    _assert_true(callable(db_connect), "db_connect should be callable.")

    _clear_graph(fresh)


def test_project_visible_domain_filtering():
    ws = Workspace.__new__(Workspace)
    ws._stores = {
        "alpha": type("DummyStore", (), {"project_name": "alpha"})(),
        "beta": type("DummyStore", (), {"project_name": "beta"})(),
    }

    _assert_equal(
        [store.project_name for store in ws._selected_stores(query="MATCH (n) RETURN n")],
        ["alpha", "beta"],
        "No project filter should keep the full active project domain.",
    )
    _assert_equal(
        [
            store.project_name
            for store in ws._selected_stores(query="MATCH (n {project: 'alpha'}) RETURN n")
        ],
        ["alpha"],
        "Cypher project filters should narrow within the active domain.",
    )
    _assert_equal(
        ws._selected_stores(query="MATCH (n {project: 'gamma'}) RETURN n"),
        [],
        "Cypher project filters outside the active domain should see nothing.",
    )
    _assert_equal(
        [
            store.project_name
            for store in ws._selected_stores(
                query="MATCH (n) WHERE n.project IN $projects RETURN n",
                params={"projects": ["beta", "gamma"]},
            )
        ],
        ["beta"],
        "Parameterized project filters should be intersected with the active domain.",
    )


def test_project_node_property_and_reserved_mutation(ws: Workspace):
    rows = ws.cypher(
        "MATCH (f:file) RETURN count(f) AS nodes",
        project="alpha",
    )
    _assert_true(rows[0]["nodes"] > 0, "Published nodes should be visible through the active project scope.")

    project_rows = ws.cypher(
        "MATCH (f:file) WHERE f.project IS NOT NULL RETURN count(f) AS nodes",
        project="alpha",
    )
    _assert_equal(project_rows, rows, "Published nodes should carry the reserved project property.")

    try:
        ws.cypher("MATCH (n) SET n.project = 'beta' RETURN n", project="alpha")
    except ValueError:
        pass
    else:
        raise AssertionError("User Cypher should not be able to mutate the reserved project property.")


def test_project_database_config(alpha_root: Path, beta_root: Path):
    alpha_graph = _graph_config()
    beta_graph = _graph_config()
    alpha_graph.database = "alpha-graph"
    beta_graph.database = "beta-graph"

    config = StoreConfig(
        projects={
            "alpha": ProjectConfig(
                name="alpha",
                source=SourceConfig(type="fs", path=str(alpha_root)),
                graph=alpha_graph,
            ),
            "beta": ProjectConfig(
                name="beta",
                source=SourceConfig(type="fs", path=str(beta_root)),
                graph=beta_graph,
            ),
        }
    )

    _assert_equal(config.projects["alpha"].graph.database, "alpha-graph")
    _assert_equal(config.projects["beta"].graph.database, "beta-graph")


def test_graph_defaults_config(tmp_root: Path):
    config_path = tmp_root / "pontis.yml"
    config_path.write_text(
        """
graph_defaults:
  uri: bolt://localhost:7999
  database: neo4j
  user: neo4j
  password_env: NEO4J_PASSWORD

projects:
  inherited:
    source:
      type: fs
      path: inherited
  override:
    source:
      type: fs
      path: override
    graph:
      uri: bolt://localhost:8000
""",
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    _assert_equal(config.projects["inherited"].graph.uri, "bolt://localhost:7999")
    _assert_equal(config.projects["inherited"].graph.database, "neo4j")
    _assert_equal(config.projects["inherited"].graph.password_env, "NEO4J_PASSWORD")
    _assert_equal(config.projects["override"].graph.uri, "bolt://localhost:8000")
    _assert_equal(config.projects["override"].graph.database, "neo4j")


def test_source_adapter_root_confinement(alpha_root: Path, beta_root: Path):
    adapter = LocalSourceAdapter(str(alpha_root))
    try:
        adapter.absolute_path(f"../{beta_root.name}")
    except ValueError:
        pass
    else:
        raise AssertionError("LocalSourceAdapter should reject paths outside source root.")


def test_pointer_resolution(ws: Workspace):
    file_open = ws.cypher(
        "MATCH (t:text {path: 'README.md'}) RETURN t._file_open AS open_file",
        project="alpha",
    )[0]["open_file"]
    _assert_true(isinstance(file_open, FileOpen), "file_open pointer should resolve on text files.")
    with file_open("r", encoding="utf-8") as f:
        _assert_true("alpha" in f.read(), "file_open should accept normal open() parameters.")

    table = ws.cypher(
        "MATCH (t:table {name: 'orders'}) RETURN t",
        project="alpha",
    )[0]["t"]
    table_connect = table["_db_connect"]
    _assert_true(isinstance(table_connect, DbConnect), "table db_connect pointer should resolve.")
    _assert_equal(table_connect.table, "orders", "DB table node context should be preserved.")
    _assert_true(callable(table_connect), "DB table connect should be callable.")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        alpha = tmp_root / "alpha"
        beta = tmp_root / "beta"
        alpha.mkdir()
        beta.mkdir()
        _prepare_alpha(alpha)
        _prepare_beta(beta)

        ws = _build_workspace({"alpha": alpha})
        _clear_graph(ws)

        test_single_project_complex(ws)
        test_idempotent_refresh(ws)
        test_same_name_paths_and_label_merging(ws)
        test_query_trigger_order_and_pointer_resolution(ws)
        test_project_visible_domain_filtering()
        test_project_node_property_and_reserved_mutation(ws)
        test_project_database_config(alpha, beta)
        test_graph_defaults_config(tmp_root)
        test_source_adapter_root_confinement(alpha, beta)
        test_pointer_resolution(ws)

        _clear_graph(ws)
    print("storage neo4j integration tests passed")


if __name__ == "__main__":
    main()
