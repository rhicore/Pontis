"""Store 层综合测试 — 纯 Store API，不依赖 tool 层。

覆盖：CRUD、边、Cypher 查询（含多跳/变长路径/参数化）、
     虚实体、跨项目边、并发控制、持久化。

用法: python3 tests/test_store.py
"""
import os
import sys
import shutil
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from storage.stores.fs import FSStore
from storage.workspace import Workspace
from storage.config import StoreConfig, ProjectConfig, SourceConfig, GraphConfig
from storage import stores as store_factory

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


def fresh_copy(src):
    tmp = tempfile.mkdtemp(prefix="pontis_test_")
    shutil.copytree(src, tmp, dirs_exist_ok=True)
    return tmp


def empty_project():
    tmp = tempfile.mkdtemp(prefix="pontis_empty_")
    os.makedirs(os.path.join(tmp, ".pontis"), exist_ok=True)
    return tmp


def make_store(path):
    """Helper: 从路径创建 FSStore。"""
    return store_factory.create_store(
        ProjectConfig(source=SourceConfig(path=path)))


# ─── 1. CRUD ───────────────────────────────────────────────

def test_crud(s):
    print("\n[1] CRUD")

    eid = s._create_node("t1", meta={"v": 1}, labels=["test"])
    ok("create returns ent_", eid.startswith("ent_"))

    m = s._get_meta("t1")
    ok("get_meta by name", m is not None and m["v"] == 1)
    ok("get_meta by id", s._get_meta(eid) is not None)

    s._set_meta("t1", {"v": 2, "extra": True})
    m2 = s._get_meta("t1")
    ok("set_meta merge", m2["v"] == 2 and m2["extra"] is True)

    s._put_meta("t1", {"v": 3, "_labels": ["test"]})
    m3 = s._get_meta("t1")
    ok("put_meta replace", m3["v"] == 3 and "extra" not in m3)

    eid2 = s._create_node("t1", labels=["test"])
    ok("idempotent create", eid == eid2)

    name = s._delete_node("t1")
    ok("delete returns name", name == "t1")
    ok("deleted → None", s._get_meta("t1") is None)
    ok("delete nonexistent → ''", s._delete_node("no_such") == "")


# ─── 2. Edges ──────────────────────────────────────────────

def test_edges(s):
    print("\n[2] Edges")

    s._create_node("a")
    s._create_node("b")
    s._create_node("c")

    s._add_edges([{"a": "a", "b": "b"}, {"a": "b", "b": "c"}])
    ok("neighbor forward", "b" in s._neighbors("a"))
    ok("neighbor bidirectional", "a" in s._neighbors("b") and "c" in s._neighbors("b"))

    s._add_edges([{"a": "a", "b": "b"}])
    ok("edge dedup", len(s._neighbors("a")) == 1)

    s._add_edges([{"a": "a", "b": "a"}])
    ok("self-loop ignored", "a" not in s._neighbors("a"))

    s._delete_node("c")
    ok("delete cascades edges", "c" not in s._neighbors("b"))

    ok("get_edges non-empty", len(s._get_edges()) >= 1)

    s._clear_edges()
    ok("clear_edges", len(s._neighbors("a")) == 0)

    s._delete_node("a")
    s._delete_node("b")


# ─── Graph builder ─────────────────────────────────────────

def build_graph(s):
    """e-commerce 图: db→tables→cols, FK, overlap, disambig, knowledge.

    shop (db)
    ├── users (table, 1000 rows, 4 cols)
    │   ├── id      (col, INT, card=1000)
    │   ├── name    (col, TEXT, card=980)
    │   ├── email   (col, TEXT, card=1000)
    │   └── age     (col, INT, card=45)
    ├── orders (table, 50000 rows, 6 cols)
    │   ├── id      (col, INT, card=50000)
    │   ├── user_id (col, INT, card=800)  ──fk──▸ users.id
    │   ├── amount  (col, REAL, card=12000) ──overlap──▸ products.price
    │   ├── status  (col, TEXT, card=5)
    │   ├── note    (col, TEXT, card=200)
    │   └── ts      (col, TEXT, card=45000)
    └── products (table, 300 rows, 5 cols)
        ├── id      (col, INT, card=300)
        ├── name    (col, TEXT, card=300) ──disambig──▸ users.name
        ├── price   (col, REAL, card=250)
        ├── cat     (col, TEXT, card=15)
        └── stock   (col, INT, card=80)

    + knowledge: convention, pattern
    """
    s._create_node("shop", labels=["db"], meta={"path": "shop.sqlite"})

    for t, rc, cc in [("users", 1000, 4), ("orders", 50000, 6), ("products", 300, 5)]:
        s._create_node(t, labels=["table"], meta={"row_count": rc, "column_count": cc})

    cols = [
        ("users", "id",       ["col", "INT"],  {"cardinality": 1000}),
        ("users", "name",     ["col", "TEXT"],  {"cardinality": 980}),
        ("users", "email",    ["col", "TEXT"],  {"cardinality": 1000}),
        ("users", "age",      ["col", "INT"],   {"cardinality": 45}),
        ("orders", "id",      ["col", "INT"],   {"cardinality": 50000}),
        ("orders", "user_id", ["col", "INT"],   {"cardinality": 800}),
        ("orders", "amount",  ["col", "REAL"],  {"cardinality": 12000}),
        ("orders", "status",  ["col", "TEXT"],  {"cardinality": 5}),
        ("orders", "note",    ["col", "TEXT"],  {"cardinality": 200}),
        ("orders", "ts",      ["col", "TEXT"],  {"cardinality": 45000}),
        ("products", "id",    ["col", "INT"],   {"cardinality": 300}),
        ("products", "name",  ["col", "TEXT"],  {"cardinality": 300}),
        ("products", "price", ["col", "REAL"],  {"cardinality": 250}),
        ("products", "cat",   ["col", "TEXT"],  {"cardinality": 15}),
        ("products", "stock", ["col", "INT"],   {"cardinality": 80}),
    ]
    for tbl, cname, lbls, meta in cols:
        full = f"{tbl}.{cname}"
        s._create_node(full, labels=lbls, meta=meta)
        s._add_edges([{"a": tbl, "b": full}])

    for t in ["users", "orders", "products"]:
        s._add_edges([{"a": "shop", "b": t}])

    # FK: orders.user_id → users.id
    s._create_node("fk_o_u", labels=["fk"], meta={"from": "orders.user_id", "to": "users.id"})
    s._add_edges([
        {"a": "orders.user_id", "b": "fk_o_u"},
        {"a": "users.id", "b": "fk_o_u"},
    ])

    # overlap: products.price ↔ orders.amount
    s._create_node("ol_price_amt", labels=["overlap"], meta={"similarity": 0.85})
    s._add_edges([
        {"a": "products.price", "b": "ol_price_amt"},
        {"a": "orders.amount", "b": "ol_price_amt"},
    ])

    # disambiguation: users.name vs products.name
    s._create_node("dis_name", labels=["disambig"],
                   meta={"note": "users.name=person, products.name=item"})
    s._add_edges([
        {"a": "users.name", "b": "dis_name"},
        {"a": "products.name", "b": "dis_name"},
    ])

    # knowledge
    s._create_node("conv_id", labels=["convention"],
                   meta={"content": "All id columns are INT auto-increment PKs"})
    s._create_node("pat_ts", labels=["pattern"],
                   meta={"content": "Timestamps use ISO 8601"})


# ─── 3. Cypher MATCH ──────────────────────────────────────

def test_cypher_match(s):
    print("\n[3] Cypher MATCH")
    build_graph(s)

    # label filter — exact set (empty project, no virtual pollution)
    r = s.cypher("MATCH (n:table) RETURN n")
    names = {row["n"]["name"] for row in r}
    ok("MATCH :table", names == {"users", "orders", "products"}, f"got {names}")

    # multi-label
    r = s.cypher("MATCH (n:col:INT) RETURN n")
    int_names = {row["n"]["name"] for row in r}
    ok("MATCH :col:INT",
       int_names == {"users.id", "users.age", "orders.id", "orders.user_id",
                      "products.id", "products.stock"},
       f"got {int_names}")

    # inline property
    r = s.cypher("MATCH (n:table {row_count: 1000}) RETURN n")
    ok("inline prop", len(r) == 1 and r[0]["n"]["name"] == "users")

    # WHERE = > <= != STARTS/ENDS/CONTAINS
    r = s.cypher("MATCH (n:table) WHERE n.row_count = 50000 RETURN n")
    ok("WHERE =", len(r) == 1 and r[0]["n"]["name"] == "orders")

    r = s.cypher("MATCH (n:table) WHERE n.row_count > 1000 RETURN n")
    ok("WHERE >", {row["n"]["name"] for row in r} == {"orders"})

    r = s.cypher("MATCH (n:table) WHERE n.row_count <= 300 RETURN n")
    ok("WHERE <=", {row["n"]["name"] for row in r} == {"products"})

    r = s.cypher("MATCH (n:table) WHERE n.name != 'orders' RETURN n")
    ok("WHERE !=", {row["n"]["name"] for row in r} == {"users", "products"})

    r = s.cypher("MATCH (n) WHERE n.name STARTS WITH 'orders.' RETURN n")
    ok("STARTS WITH", len(r) == 6, f"got {len(r)}")  # id,user_id,amount,status,note,ts

    r = s.cypher("MATCH (n:col) WHERE n.name ENDS WITH '.id' RETURN n")
    ok("ENDS WITH", len(r) == 3, f"got {[x['n']['name'] for x in r]}")

    r = s.cypher("MATCH (n:col) WHERE n.name CONTAINS '.user' RETURN n")
    user_cols = {row["n"]["name"] for row in r}
    ok("CONTAINS", user_cols == {"orders.user_id"}, f"got {user_cols}")

    # >= / <
    r = s.cypher("MATCH (n:col:INT) WHERE n.cardinality >= 1000 RETURN n")
    big_ints = {row["n"]["name"] for row in r}
    ok("WHERE >=", "users.id" in big_ints and "orders.id" in big_ints, f"got {big_ints}")

    r = s.cypher("MATCH (n:col:INT) WHERE n.cardinality < 100 RETURN n")
    small_ints = {row["n"]["name"] for row in r}
    ok("WHERE <", small_ints == {"users.age", "products.stock"}, f"got {small_ints}")


# ─── 4. Cypher traversal ──────────────────────────────────

def test_cypher_traversal(s):
    print("\n[4] Cypher Traversal")

    # 1-hop: db → table
    r = s.cypher("MATCH (d:db)--(t:table) RETURN d, t")
    ok("1-hop", len(r) == 3, f"got {len(r)}")
    ok("1-hop var d", all(row["d"]["name"] == "shop" for row in r))

    # 2-hop: db → table → col
    r = s.cypher("MATCH (d:db)--(t:table)--(c:col) RETURN c")
    ok("2-hop", len(r) == 15, f"got {len(r)}")

    # 3-hop: db → table → col → disambig
    r = s.cypher("MATCH (d:db)--(t:table)--(c:col)--(x:disambig) RETURN c, x")
    ok("3-hop disambig", len(r) == 2, f"got {len(r)}")
    c_names = {row["c"]["name"] for row in r}
    ok("disambig cols", c_names == {"users.name", "products.name"})

    # FK path (undirected: both directions match)
    r = s.cypher("MATCH (c1:col)--(fk:fk)--(c2:col) RETURN c1, c2")
    ok("FK path count", len(r) == 2, f"got {len(r)}")
    pairs = {(row["c1"]["name"], row["c2"]["name"]) for row in r}
    ok("FK endpoints",
       ("orders.user_id", "users.id") in pairs or ("users.id", "orders.user_id") in pairs,
       f"got {pairs}")

    # overlap path
    r = s.cypher("MATCH (c1:col)--(o:overlap)--(c2:col) RETURN c1, c2")
    ok("overlap path count", len(r) == 2, f"got {len(r)}")
    overlap_pair = {(row["c1"]["name"], row["c2"]["name"]) for row in r}
    ok("overlap endpoints",
       ("products.price", "orders.amount") in overlap_pair
       or ("orders.amount", "products.price") in overlap_pair)

    # 4-hop: table → col → fk → col → table
    r = s.cypher("""
        MATCH (t1:table)--(c1:col)--(fk:fk)--(c2:col)--(t2:table)
        RETURN t1, t2
    """)
    ok("4-hop join path", len(r) >= 1, f"got {len(r)}")
    if r:
        pair = (r[0]["t1"]["name"], r[0]["t2"]["name"])
        ok("join path orders↔users",
           pair == ("orders", "users") or pair == ("users", "orders"),
           f"got {pair}")

    # variable-length 1..2
    r = s.cypher("MATCH (d:db)-[*1..2]-(t:table) RETURN DISTINCT t")
    ok("[*1..2]", len(r) == 3, f"got {len(r)}")

    # variable-length 1..3
    r = s.cypher("MATCH (d:db {name: 'shop'})-[*1..3]-(n) RETURN DISTINCT n")
    ok("[*1..3]", len(r) >= 15, f"got {len(r)}")

    # cartesian product + WHERE
    r = s.cypher("""
        MATCH (a:table), (b:col:INT)
        WHERE a.name = 'users' AND b.cardinality > 500
        RETURN b
    """)
    big_int = {row["b"]["name"] for row in r}
    ok("cartesian + WHERE", "users.id" in big_int and "orders.user_id" in big_int,
       f"got {big_int}")

    # knowledge
    r = s.cypher("MATCH (n:convention) RETURN n")
    ok("MATCH :convention", len(r) == 1 and r[0]["n"]["name"] == "conv_id")
    r = s.cypher("MATCH (n:pattern) RETURN n")
    ok("MATCH :pattern", len(r) == 1 and r[0]["n"]["name"] == "pat_ts")

    # no-label match by property only
    s._create_node("loner", meta={"note": "no labels"})
    r = s.cypher("MATCH (n {name: 'loner'}) RETURN n")
    ok("property-only match", len(r) == 1)
    s._delete_node("loner")


# ─── 5. Cypher writes ─────────────────────────────────────

def test_cypher_writes(s):
    print("\n[5] Cypher Writes")

    # CREATE — returns list with {"created": ...}
    r = s.cypher("CREATE (n:demo {name: 'w1', x: 10})")
    ok("CREATE node", isinstance(r, list) and r[0].get("created", {}).get("name") == "w1",
       f"got {r}")
    ok("CREATE verifiable", len(s.cypher("MATCH (n:demo) RETURN n")) == 1)

    # SET =
    s.cypher("MATCH (n:demo {name: 'w1'}) SET n.x = 20")
    m = s._get_meta("w1")
    ok("SET =", m["x"] == 20)

    # SET += $params (params dict is nested: the key "p" matches $p)
    s.cypher("MATCH (n:demo {name: 'w1'}) SET n += $p",
             params={"p": {"y": "hello", "tags": [1, 2, 3]}})
    m2 = s._get_meta("w1")
    ok("SET += merge", m2["x"] == 20 and m2["y"] == "hello")
    ok("SET += labels kept", "demo" in m2.get("_labels", []))

    # param WHERE
    r = s.cypher("MATCH (n:demo) WHERE n.y = $v RETURN n", params={"v": "hello"})
    ok("param WHERE", len(r) == 1)

    # CREATE edge
    r = s.cypher("MATCH (a {name: 'users'}), (b {name: 'w1'}) CREATE (a)--(b)")
    ok("CREATE edge", isinstance(r, list) and r[0].get("created_edges", 0) >= 1,
       f"got {r}")
    ok("edge exists", "w1" in s._neighbors("users"))

    # DELETE
    r = s.cypher("MATCH (n:demo) DELETE n")
    ok("DELETE", isinstance(r, list) and len(r[0].get("deleted", [])) >= 1,
       f"got {r}")
    ok("deleted gone", s._get_meta("w1") is None)
    ok("edge cleaned", "w1" not in s._neighbors("users"))


# ─── 6. Virtual entities (needs real FS) ──────────────────

def test_virtual(s):
    print("\n[6] Virtual Entities")

    # create a real sqlite file for virtual discovery
    import sqlite3
    db_path = os.path.join(s._project_path, "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS t1 (id INT, name TEXT)")
    conn.execute("INSERT INTO t1 VALUES (1, 'a'), (2, 'b')")
    conn.commit()
    conn.close()

    s._ensure_index()

    # file virtual should be in index
    found = False
    for vid in s._virtual_ids:
        props = s._id_index.get(vid, {})
        if props.get("name") == "test.db":
            found = True
            break
    ok("file virtual in index", found)

    # get_virtual_meta
    fm = s.get_virtual_meta("test.db")
    ok("file virtual meta", fm is not None)
    if fm:
        ok("file has file_size", "file_size" in fm)
        ok("file has db label", "db" in fm.get("_labels", []))

    # dir virtual
    dm = s.get_virtual_meta(".")
    ok("root dir virtual", dm is not None)
    if dm:
        ok("dir has child_count", "child_count" in dm, f"keys: {list(dm.keys())}")

    # materialize
    if found:
        eid = s._create_node("test.db")
        ok("materialize → ent_", eid.startswith("ent_"))
        ok("materialize persisted", s._get_meta(eid) is not None)
        eid2 = s._create_node("test.db")
        ok("materialize idempotent", eid == eid2)

    # discover_virtual for dirs
    dirs = s.discover_virtual("*", label="dir")
    ok("discover dirs", len(dirs) > 0, f"got {len(dirs)}")

    # discover_virtual for untracked files — create a new one
    with open(os.path.join(s._project_path, "new_file.csv"), "w") as f:
        f.write("a,b\n1,2\n")
    found_files = s.discover_virtual("*.csv")
    ok("discover new csv", len(found_files) > 0, f"got {len(found_files)}")


# ─── 7. Cross-project ─────────────────────────────────────

def test_cross_project():
    print("\n[7] Cross-Project Edges")

    p1_path = empty_project()
    p2_path = empty_project()

    s1 = make_store(p1_path)
    s2 = make_store(p2_path)

    id_a = s1._create_node("svc_a", labels=["service"], meta={"team": "platform"})
    id_b = s2._create_node("svc_b", labels=["service"], meta={"team": "data"})
    id_c = s2._create_node("tbl_x", labels=["table"], meta={"rows": 999})

    ws = Workspace.__new__(Workspace)
    ws._config = StoreConfig(
        projects={"p1": ProjectConfig(source=SourceConfig(path=p1_path)), "p2": ProjectConfig(source=SourceConfig(path=p2_path))})
    ws._stores = {"p1": s1, "p2": s2}

    # add cross-ref
    ws._add_cross_ref("p1", "svc_a", "p2", id_b)
    refs = s1.get_cross_refs("svc_a")
    ok("cross-ref created", len(refs) == 1 and refs[0]["to_project"] == "p2")
    ok("not stale", not refs[0]["stale"])

    # resolve OK
    res = ws._resolve_cross_refs(project="p1")
    ok_r = [r for r in res if r["status"] == "ok"]
    ok("resolve ok", len(ok_r) == 1 and ok_r[0]["to_entity"]["name"] == "svc_b")

    # second cross-ref
    ws._add_cross_ref("p1", "svc_a", "p2", id_c)
    ok("two cross-refs", len(s1.get_cross_refs("svc_a")) == 2)

    # delete target → stale
    s2._delete_node("svc_b")
    res2 = ws._resolve_cross_refs(project="p1")
    missing = [r for r in res2 if r["status"] == "target_missing"]
    ok("target_missing", len(missing) == 1)

    # purge
    purged = ws._purge_stale_refs(project="p1")
    ok("purge stale", purged == 1)
    ok("after purge", len(s1.get_cross_refs("svc_a")) == 1)

    # project unavailable
    ws2 = Workspace.__new__(Workspace)
    ws2._config = ws._config
    ws2._stores = {"p1": s1}
    res3 = ws2._resolve_cross_refs(project="p1")
    unavail = [r for r in res3 if r["status"] == "project_unavailable"]
    ok("project_unavailable", len(unavail) >= 1)

    # invalid format
    try:
        ws._add_cross_ref("p1", "svc_a", "p2", "bad_id")
        ok("invalid raises", False)
    except ValueError:
        ok("invalid raises ValueError", True)

    # delete source cleans cross-edges
    s1._delete_node("svc_a")
    ok("delete source cleans", len(s1.get_cross_refs()) == 0)

    # cross-edge persists across restart
    id_d = s2._create_node("remote", labels=["x"])
    id_e = s1._create_node("local", labels=["y"])
    ws._stores["p2"] = s2
    ws._add_cross_ref("p1", "local", "p2", id_d)

    s1_new = make_store(p1_path)
    ok("cross-edge persisted", len(s1_new.get_cross_refs("local")) == 1)

    shutil.rmtree(p1_path, ignore_errors=True)
    shutil.rmtree(p2_path, ignore_errors=True)


# ─── 8. Concurrency ───────────────────────────────────────

def test_concurrency():
    print("\n[8] Concurrency")

    p = empty_project()
    a = make_store(p)
    b = make_store(p)
    a._ensure_index()
    b._ensure_index()

    ok("same initial version", a._last_version == b._last_version)

    # A writes
    a._create_node("cnode", labels=["test"], meta={"who": "A"})
    ok("A bumps version", a._last_version > b._last_version)

    # B rebuilds
    b._ensure_index()
    ok("B catches version", b._last_version == a._last_version)
    ok("B sees A's data", b._get_meta("cnode") is not None)

    # B writes
    b._set_meta("cnode", {"who": "B", "note": "from B"})
    ok("B bumps version", b._last_version > a._last_version)

    # A rebuilds
    a._ensure_index()
    m = a._get_meta("cnode")
    ok("A sees B's write", m.get("who") == "B" and m.get("note") == "from B",
       f"got {m}")

    # version persists
    c = make_store(p)
    c._ensure_index()
    ok("version persists", c._last_version == a._last_version)

    shutil.rmtree(p, ignore_errors=True)


# ─── 9. Persistence ───────────────────────────────────────

def test_persistence():
    print("\n[9] Persistence")

    p = empty_project()
    s1 = make_store(p)
    s1._create_node("p1", labels=["p"], meta={"v": 1})
    s1._create_node("p2", labels=["p"])
    s1._add_edges([{"a": "p1", "b": "p2"}])

    s2 = make_store(p)
    ok("persisted meta", s2._get_meta("p1") is not None and s2._get_meta("p1")["v"] == 1)
    ok("persisted edge", "p2" in s2._neighbors("p1"))
    ok("persisted labels", "p" in s2._get_meta("p1").get("_labels", []))

    # cypher write across instances
    s2.cypher("MATCH (n:p) SET n.v = 2")
    s3 = make_store(p)
    ok("cypher persists", s3._get_meta("p1")["v"] == 2)

    # delete persists
    s3._delete_node("p1")
    s4 = make_store(p)
    ok("delete persists", s4._get_meta("p1") is None)
    ok("edge cleaned persists", "p2" not in s4._neighbors("p1"))

    shutil.rmtree(p, ignore_errors=True)


# ─── Main ─────────────────────────────────────────────────

def main():
    global failed

    try:
        # Sections 1-2: empty project for pure graph ops
        p = empty_project()
        s = make_store(p)
        test_crud(s)
        test_edges(s)
        shutil.rmtree(p, ignore_errors=True)

        # Sections 3-5: clean project for Cypher tests
        p = empty_project()
        s = make_store(p)
        test_cypher_match(s)
        test_cypher_traversal(s)
        test_cypher_writes(s)
        shutil.rmtree(p, ignore_errors=True)

        # Section 6: needs real FS with sqlite file
        ctx_src = os.path.join(ROOT, "example_data", "context")
        ctx_path = fresh_copy(ctx_src)
        s = make_store(ctx_path)
        test_virtual(s)
        shutil.rmtree(ctx_path, ignore_errors=True)

        # Sections 7-9: independent
        test_cross_project()
        test_concurrency()
        test_persistence()

    except Exception:
        print(f"\n💥 UNEXPECTED ERROR:")
        traceback.print_exc()
        failed += 1

    total = passed + failed
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed")
    if errors:
        print("Failed:")
        for e in errors:
            print(f"  - {e}")
    print('=' * 50)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
