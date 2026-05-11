"""Storage public contract tests.

All graph operations in this file go through `cypher(...)`.
The test intentionally avoids Store private methods: storage core must be
validated as a Cypher graph engine, not as a ref/name/path resolver.

Usage: python3 scripts/storage/test_store.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from storage import stores as store_factory
from storage.config import GraphConfig, ProjectConfig, SourceConfig
from storage.workspace import Workspace


passed = 0
failed = 0
errors = []


def ok(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        msg = f"  ✗ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        errors.append(name)


def empty_project():
    tmp = tempfile.mkdtemp(prefix="pontis_empty_")
    os.makedirs(os.path.join(tmp, ".pontis"), exist_ok=True)
    return tmp


def make_store(path):
    return store_factory.create_store(
        ProjectConfig(source=SourceConfig(type="fs", path=path)))


def make_graph_store(db_path=None):
    if db_path is None:
        root = tempfile.mkdtemp(prefix="pontis_graph_")
        db_path = os.path.join(root, "store.db")
    return store_factory.create_store(
        ProjectConfig(
            source=SourceConfig(type="graph"),
            graph=GraphConfig(type="sqlite", path=db_path),
        )
    )


def names(rows, var="n"):
    return {row[var].get("name") for row in rows}


def first(rows, var="n"):
    return rows[0][var] if rows else {}


def external_entity_shape(node):
    return isinstance(node, dict) and "id" in node and "labels" in node and "label" not in node and "eid" not in node


def create_node(s, label, **props):
    props_text = ", ".join(
        f"{k}: {repr(v)}" if not isinstance(v, (int, float)) else f"{k}: {v}"
        for k, v in props.items()
    )
    query = f"CREATE (n:{label} {{{props_text}}})" if props_text else f"CREATE (n:{label})"
    return s.cypher(query)[0]["created"]


def create_edge_by_name(s, a_name, b_name):
    return s.cypher(
        "MATCH (a {name: $a}), (b {name: $b}) CREATE (a)--(b)",
        params={"a": a_name, "b": b_name},
    )


def test_crud(s):
    print("\n[1] CRUD via Cypher")

    created = create_node(s, "test", name="t1", v=1)
    ent_id = created["id"]
    ok("CREATE returns id", ent_id.startswith("ent_"), f"got {created}")
    ok("external entity fields", external_entity_shape(created), f"got {created}")
    ok("ordinary name returned", created.get("name") == "t1", f"got {created}")

    rows = s.cypher("MATCH (n {name: $name}) RETURN n", params={"name": "t1"})
    ok("MATCH by ordinary name", len(rows) == 1 and rows[0]["n"]["id"] == ent_id, f"got {rows}")

    rows_by_id = s.cypher("MATCH (n {id: $id}) RETURN n", params={"id": ent_id})
    ok("MATCH by id", len(rows_by_id) == 1 and rows_by_id[0]["n"]["name"] == "t1")

    s.cypher("MATCH (n {id: $id}) SET n.v = 2, n.extra = 'yes'", params={"id": ent_id})
    updated = first(s.cypher("MATCH (n {id: $id}) RETURN n", params={"id": ent_id}))
    ok("SET ordinary props", updated.get("v") == 2 and updated.get("extra") == "yes", f"got {updated}")

    s.cypher("MATCH (n {id: $id}) SET n.id = 'bad_id'", params={"id": ent_id})
    same_id = first(s.cypher("MATCH (n {id: $id}) RETURN n", params={"id": ent_id}))
    bad_id = s.cypher("MATCH (n {id: 'bad_id'}) RETURN n")
    ok("SET id ignored", same_id.get("id") == ent_id and bad_id == [], f"got {same_id} / {bad_id}")

    s.cypher("MATCH (n {id: $id}) SET n.labels = $labels", params={"id": ent_id, "labels": ["test", "updated"]})
    relabeled = first(s.cypher("MATCH (n {id: $id}) RETURN n", params={"id": ent_id}))
    ok("SET labels", set(relabeled.get("labels", [])) == {"test", "updated"}, f"got {relabeled}")

    s.cypher("MATCH (n {id: $id}) DELETE n", params={"id": ent_id})
    gone = s.cypher("MATCH (n {id: $id}) RETURN n", params={"id": ent_id})
    ok("DELETE by id", gone == [], f"got {gone}")


def test_edges(s):
    print("\n[2] Edges via Cypher")

    for name in ("a", "b", "c"):
        create_node(s, "node", name=name)
    create_edge_by_name(s, "a", "b")
    create_edge_by_name(s, "b", "c")
    create_edge_by_name(s, "a", "b")

    rows = s.cypher("MATCH (a {name: 'a'})--(b) RETURN b")
    ok("neighbor forward", names(rows, "b") == {"b"}, f"got {rows}")

    rows = s.cypher("MATCH (b {name: 'b'})--(n) RETURN n")
    ok("neighbor bidirectional and dedup", names(rows) == {"a", "c"}, f"got {rows}")

    s.cypher("MATCH (n {name: 'c'}) DELETE n")
    rows = s.cypher("MATCH (b {name: 'b'})--(n) RETURN n")
    ok("delete cascades edges", names(rows) == {"a"}, f"got {rows}")


def build_graph(s):
    create_node(s, "db", name="shop", path="shop.sqlite")
    for table, rc, cc in [("users", 1000, 4), ("orders", 50000, 6), ("products", 300, 5)]:
        create_node(s, "table", name=table, row_count=rc, column_count=cc)
        create_edge_by_name(s, "shop", table)

    cols = [
        ("users", "id", ["col", "INT"], {"cardinality": 1000}),
        ("users", "name", ["col", "TEXT"], {"cardinality": 980}),
        ("users", "email", ["col", "TEXT"], {"cardinality": 1000}),
        ("users", "age", ["col", "INT"], {"cardinality": 45}),
        ("orders", "id", ["col", "INT"], {"cardinality": 50000}),
        ("orders", "user_id", ["col", "INT"], {"cardinality": 800}),
        ("orders", "amount", ["col", "REAL"], {"cardinality": 12000}),
        ("orders", "status", ["col", "TEXT"], {"cardinality": 5}),
        ("orders", "note", ["col", "TEXT"], {"cardinality": 200}),
        ("orders", "ts", ["col", "TEXT"], {"cardinality": 45000}),
        ("products", "id", ["col", "INT"], {"cardinality": 300}),
        ("products", "name", ["col", "TEXT"], {"cardinality": 300}),
        ("products", "price", ["col", "REAL"], {"cardinality": 250}),
        ("products", "cat", ["col", "TEXT"], {"cardinality": 15}),
        ("products", "stock", ["col", "INT"], {"cardinality": 80}),
    ]
    for table, col, labels, props in cols:
        full = f"{table}.{col}"
        label_text = ":".join(labels)
        props_text = ", ".join([f"name: {full!r}"] + [f"{k}: {v!r}" for k, v in props.items()])
        s.cypher(f"CREATE (n:{label_text} {{{props_text}}})")
        create_edge_by_name(s, table, full)

    create_node(s, "fk", name="fk_o_u")
    create_edge_by_name(s, "orders.user_id", "fk_o_u")
    create_edge_by_name(s, "users.id", "fk_o_u")

    create_node(s, "overlap", name="ol_price_amt", similarity=0.85)
    create_edge_by_name(s, "products.price", "ol_price_amt")
    create_edge_by_name(s, "orders.amount", "ol_price_amt")

    create_node(s, "disambig", name="dis_name", note="users.name=person, products.name=item")
    create_edge_by_name(s, "users.name", "dis_name")
    create_edge_by_name(s, "products.name", "dis_name")

    create_node(s, "convention", name="conv_id", content="All id columns are INT auto-increment PKs")
    create_node(s, "pattern", name="pat_ts", content="Timestamps use ISO 8601")


def test_cypher_match_and_traversal(s):
    print("\n[3] Cypher Match and Traversal")
    build_graph(s)

    ok("MATCH :table", names(s.cypher("MATCH (n:table) RETURN n")) == {"users", "orders", "products"})
    ok("MATCH :col:INT", names(s.cypher("MATCH (n:col:INT) RETURN n")) == {
        "users.id", "users.age", "orders.id", "orders.user_id", "products.id", "products.stock"
    })
    ok("inline prop", first(s.cypher("MATCH (n:table {row_count: 1000}) RETURN n")).get("name") == "users")
    ok("WHERE =", first(s.cypher("MATCH (n:table) WHERE n.row_count = 50000 RETURN n")).get("name") == "orders")
    ok("WHERE >", names(s.cypher("MATCH (n:table) WHERE n.row_count > 1000 RETURN n")) == {"orders"})
    ok("WHERE <=", names(s.cypher("MATCH (n:table) WHERE n.row_count <= 300 RETURN n")) == {"products"})
    ok("WHERE !=", names(s.cypher("MATCH (n:table) WHERE n.name != 'orders' RETURN n")) == {"users", "products"})
    ok("STARTS WITH", len(s.cypher("MATCH (n) WHERE n.name STARTS WITH 'orders.' RETURN n")) == 6)
    ok("ENDS WITH", len(s.cypher("MATCH (n:col) WHERE n.name ENDS WITH '.id' RETURN n")) == 3)
    ok("CONTAINS", names(s.cypher("MATCH (n:col) WHERE n.name CONTAINS '.user' RETURN n")) == {"orders.user_id"})

    ok("1-hop", len(s.cypher("MATCH (d:db)--(t:table) RETURN d, t")) == 3)
    ok("2-hop", len(s.cypher("MATCH (d:db)--(t:table)--(c:col) RETURN c")) == 15)
    ok("3-hop disambig", names(s.cypher("MATCH (d:db)--(t:table)--(c:col)--(x:disambig) RETURN c"), "c") == {"users.name", "products.name"})
    ok("FK path", len(s.cypher("MATCH (c1:col)--(fk:fk)--(c2:col) RETURN c1, c2")) == 2)
    ok("overlap path", len(s.cypher("MATCH (c1:col)--(o:overlap)--(c2:col) RETURN c1, c2")) == 2)
    ok("varlen", len(s.cypher("MATCH (d:db {name: 'shop'})-[*1..3]-(n) RETURN DISTINCT n")) >= 15)
    ok("cartesian + WHERE", "orders.user_id" in names(s.cypher("""
        MATCH (a:table), (b:col:INT)
        WHERE a.name = 'users' AND b.cardinality > 500
        RETURN b
    """), "b"))


def test_cypher_writes(s):
    print("\n[4] Cypher Writes")

    created = s.cypher("CREATE (n:demo {name: 'w1', x: 10})")[0]["created"]
    ok("CREATE node", created.get("name") == "w1" and created.get("x") == 10, f"got {created}")

    s.cypher("MATCH (n:demo {name: 'w1'}) SET n.x = 20")
    ok("SET =", first(s.cypher("MATCH (n:demo {name: 'w1'}) RETURN n")).get("x") == 20)

    s.cypher("MATCH (n:demo {name: 'w1'}) SET n += $p", params={"p": {"y": "hello", "tags": [1, 2, 3]}})
    row = first(s.cypher("MATCH (n:demo {name: 'w1'}) RETURN n"))
    ok("SET += merge", row.get("x") == 20 and row.get("y") == "hello", f"got {row}")
    ok("param WHERE", len(s.cypher("MATCH (n:demo) WHERE n.y = $v RETURN n", params={"v": "hello"})) == 1)

    create_edge_by_name(s, "users", "w1")
    ok("CREATE edge", len(s.cypher("MATCH (a {name: 'users'})--(b {name: 'w1'}) RETURN a, b")) == 1)

    s.cypher("MATCH (n:demo {name: 'w1'}) DELETE n")
    ok("DELETE", s.cypher("MATCH (n:demo {name: 'w1'}) RETURN n") == [])
    ok("edge cleaned", s.cypher("MATCH (a {name: 'users'})--(b {name: 'w1'}) RETURN a, b") == [])


def test_virtual_and_src():
    print("\n[5] Virtual Entities and src via Workspace.cypher")

    p = empty_project()
    db_path = os.path.join(p, "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE orders(order_id INTEGER, user_id INTEGER, FOREIGN KEY(user_id) REFERENCES users(id))")
    conn.execute("INSERT INTO users VALUES (1, 'a'), (2, 'b')")
    conn.execute("INSERT INTO orders VALUES (10, 1), (11, 2)")
    conn.commit()
    conn.close()

    txt_path = os.path.join(p, "notes.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("hello\nworld\n")

    ws = Workspace(project_path=p)
    db_rows = ws.cypher("MATCH (f:file:db) WHERE f.name = 'test.db' RETURN f, f.src AS src")
    ok("db file virtual row", len(db_rows) == 1, f"got {db_rows}")
    if db_rows:
        f = db_rows[0]["f"]
        src = db_rows[0]["src"]
        ok("virtual file external fields", external_entity_shape(f), f"got {f}")
        ok("virtual file ordinary props", f.get("name") == "test.db" and f.get("path") == "test.db", f"got {f}")
        ok("src db_connect", src is not None and src.has("db_connect"))

    table_rows = ws.cypher('MATCH (d:file:db {name: "test.db"})--(t:table) RETURN t')
    ok("db schema tables", names(table_rows, "t") == {"users", "orders"}, f"got {table_rows}")

    col_rows = ws.cypher("""
        MATCH (d:file:db {name: "test.db"})--(t:table {name: "users"})--(c:col {name: "id"})
        RETURN c
    """)
    ok("db schema column by ordinary props", len(col_rows) == 1, f"got {col_rows}")
    if col_rows:
        ok("db schema external fields", external_entity_shape(col_rows[0]["c"]), f"got {col_rows[0]['c']}")

    fk_rows = ws.cypher("MATCH (n:fk) RETURN n")
    ok("db schema fk", "orders.user_id->users.id" in names(fk_rows), f"got {fk_rows}")

    text_rows = ws.cypher("MATCH (f:file) WHERE f.name = 'notes.txt' RETURN f.src AS src")
    ok("text file src row", len(text_rows) == 1, f"got {text_rows}")
    if text_rows:
        src = text_rows[0]["src"]
        with src.get("open")("r", encoding="utf-8") as fh:
            content = fh.read()
        ok("text src open", content == "hello\nworld\n", f"got {content!r}")

    ws.cypher("MATCH (f:file {name: 'notes.txt'}) SET f.note = 'kept'")
    note_rows = ws.cypher("MATCH (f:file {name: 'notes.txt'}) RETURN f")
    ok("Cypher SET materializes virtual", first(note_rows, "f").get("note") == "kept", f"got {note_rows}")

    edge_rows = ws.cypher(
        "MATCH (a:file {name: 'notes.txt'}), (b:file:db {name: 'test.db'}) CREATE (a)--(b)"
    )
    linked = ws.cypher("MATCH (a:file {name: 'notes.txt'})--(b:file:db {name: 'test.db'}) RETURN a, b")
    ok("Cypher CREATE edge materializes virtual endpoints", edge_rows and len(linked) == 1, f"got {edge_rows}, {linked}")

    shutil.rmtree(p, ignore_errors=True)


def test_graph_only():
    print("\n[6] Graph-only Project")

    root = tempfile.mkdtemp(prefix="pontis_graph_only_")
    db_path = os.path.join(root, "store.db")
    s = make_graph_store(db_path)
    s.cypher("CREATE (n:knowledge {name: 'README', brief: 'x', detail: 'y'})")

    rows = s.cypher("MATCH (n:knowledge) RETURN n")
    ok("graph-only cypher", len(rows) == 1 and rows[0]["n"]["name"] == "README", f"got {rows}")
    rows2 = s.cypher("MATCH (n:knowledge) RETURN n.src AS src")
    ok("graph-only src is None", len(rows2) == 1 and rows2[0]["src"] is None, f"got {rows2}")

    shutil.rmtree(root, ignore_errors=True)


def test_concurrency_and_persistence():
    print("\n[7] Persistence and Concurrent Visibility")

    p = empty_project()
    a = make_store(p)
    b = make_store(p)

    a.cypher("CREATE (n:p {name: 'p1', v: 1})")
    b_rows = b.cypher("MATCH (n:p {name: 'p1'}) RETURN n")
    ok("second store sees create", len(b_rows) == 1 and b_rows[0]["n"]["v"] == 1, f"got {b_rows}")

    b.cypher("MATCH (n:p {name: 'p1'}) SET n.v = 2")
    a_rows = a.cypher("MATCH (n:p {name: 'p1'}) RETURN n")
    ok("first store sees update", len(a_rows) == 1 and a_rows[0]["n"]["v"] == 2, f"got {a_rows}")

    b.cypher("CREATE (n:p {name: 'p2'})")
    b.cypher("MATCH (a {name: 'p1'}), (b {name: 'p2'}) CREATE (a)--(b)")
    c = make_store(p)
    ok("persisted meta", first(c.cypher("MATCH (n:p {name: 'p1'}) RETURN n")).get("v") == 2)
    ok("persisted edge", len(c.cypher("MATCH (a {name: 'p1'})--(b {name: 'p2'}) RETURN a, b")) == 1)

    c.cypher("MATCH (n:p {name: 'p1'}) DELETE n")
    d = make_store(p)
    ok("delete persists", d.cypher("MATCH (n:p {name: 'p1'}) RETURN n") == [])
    ok("edge cleaned persists", d.cypher("MATCH (a {name: 'p1'})--(b {name: 'p2'}) RETURN a, b") == [])

    shutil.rmtree(p, ignore_errors=True)


def main():
    try:
        p = empty_project()
        s = make_store(p)
        test_crud(s)
        test_edges(s)
        test_cypher_match_and_traversal(s)
        test_cypher_writes(s)
        shutil.rmtree(p, ignore_errors=True)

        test_virtual_and_src()
        test_graph_only()
        test_concurrency_and_persistence()

    except Exception:
        print("\n💥 UNEXPECTED ERROR:")
        traceback.print_exc()
        return 1

    print("\n" + "=" * 50)
    print(f"Results: {passed}/{passed + failed} passed")
    if errors:
        print("Failed:")
        for e in errors:
            print(f"  - {e}")
    print("=" * 50)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
