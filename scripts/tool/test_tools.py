"""Tool layer regression tests.

Coverage:
- path/file ref resolution for file / table / column entities
- read tools: find / meta / query / cypher / grep
- write tools: update_meta / create_entity / add_edge / delete
- negative cases for common agent mistakes

Usage: python3 scripts/tool/test_tools.py
"""

import os
import json
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from storage.workspace import Workspace
from agent.agent import PontusAgent
from agent.guardrail.sql_utils import get_meta_read
from extractor.utils.refs import get_entity_meta
from tool.query.tool import query_command
from tool.grep.tool import grep_command
from tool.read.tool import read_command
from tool.jd.tool import jd_command
from tool.add_edge.tool import add_edge_command
from tool.create_entity.tool import create_entity_command
from tool.cypher.tool import cypher_command
from tool.delete.tool import delete_command
from tool.find.tool import find_command
from tool.utils.knowledge_meta import normalize_knowledge_meta
from tool.meta.tool import meta_command
from tool.update_meta.tool import update_meta_command

passed = 0
failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        suffix = f" — {detail}" if detail else ""
        print(f"  ✗ {name}{suffix}")


def make_books_project():
    tmp = tempfile.mkdtemp(prefix="pontis_tool_test_")

    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# Books\n\n"
            "This fixture mentions address status and order status.\n"
            "Use it to test grep and read tools.\n"
        )

    os.makedirs(os.path.join(tmp, "notes"), exist_ok=True)
    with open(os.path.join(tmp, "notes", "glossary.txt"), "w", encoding="utf-8") as fh:
        fh.write("address status domain\norder status domain\n")

    with open(os.path.join(tmp, "binary.bin"), "wb") as fh:
        fh.write(b"\x00\xfforder status must never be searched as text\x00")

    with open(os.path.join(tmp, "records.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "table": "status_records",
                "records": [
                    {"id": 1, "name": "Active", "kind": "address", "score": 10},
                    {"id": 2, "name": "Pending Delivery", "kind": "order", "score": 20},
                    {"id": 3, "name": "No Score", "kind": "order", "score": None},
                ],
            },
            fh,
        )

    with open(os.path.join(tmp, "orders.csv"), "w", encoding="utf-8") as fh:
        fh.write(
            "order_id,status,amount\n"
            "100,received,12.5\n"
            "101,delivered,30\n"
            "102,delivered,7.5\n"
            "103,delivered,\n"
        )

    db_path = os.path.join(tmp, "books.sqlite")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE address_status (
            status_id INTEGER PRIMARY KEY,
            address_status TEXT NOT NULL
        );
        CREATE TABLE customer_address (
            customer_id INTEGER NOT NULL,
            address_id INTEGER NOT NULL,
            status_id INTEGER NOT NULL,
            FOREIGN KEY(status_id) REFERENCES address_status(status_id)
        );
        CREATE TABLE order_status (
            status_id INTEGER PRIMARY KEY,
            status_value TEXT NOT NULL
        );
        CREATE TABLE order_history (
            history_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            status_id INTEGER NOT NULL,
            FOREIGN KEY(status_id) REFERENCES order_status(status_id)
        );
        INSERT INTO address_status(status_id, address_status) VALUES
            (1, 'Active'),
            (2, 'Inactive');
        INSERT INTO customer_address(customer_id, address_id, status_id) VALUES
            (1, 10, 1),
            (2, 11, 2);
        INSERT INTO order_status(status_id, status_value) VALUES
            (1, 'Order Received'),
            (2, 'Pending Delivery'),
            (3, 'Delivered');
        INSERT INTO order_history(history_id, order_id, status_id) VALUES
            (1, 100, 1),
            (2, 100, 2),
            (3, 101, 3);
        """
    )
    conn.commit()
    conn.close()
    return tmp


def cleanup_test_graph(ws):
    # The temporary project name is unique, so source nodes cannot be stale.
    # A write triggers source publication before executing; deleting source
    # refs here would therefore erase the freshly published fixture itself.
    ws.cypher(
        "MATCH (n) "
        "WHERE (n:chunk AND n.name IN ['0001', '0002']) "
        "OR n.name IN $names "
        "DETACH DELETE n",
        params={
            "names": [
                "status_id_domain",
                "status_id",
                "knowledge_rule",
                "knowledge_case",
                "knowledge_case_sparse",
                "evidence_literal_test",
                "value_domain_test",
            ]
        },
    )


def main():
    project = make_books_project()
    ws = Workspace(project_path=project)
    # Dynamic projects share the configured test graph. Source identities are
    # intentionally project-independent, so remove prior test-owned nodes
    # before publishing this run's fixture.
    ws._get_store().execute_cypher(
        "MATCH (n) WHERE n.project STARTS WITH 'pontis_tool_test_' DETACH DELETE n"
    )
    ws._get_store().invalidate_modules()
    ws.refresh_sources()
    cleanup_test_graph(ws)

    print("[1] Ref resolution")
    file_meta = meta_command(ws, "books.sqlite", all=True)
    ok("meta resolves books.sqlite file ref", "Error:" not in file_meta and "No metadata found" not in file_meta, file_meta)

    col_meta = meta_command(ws, "books.sqlite/address_status/status_id", all=True)
    ok("meta resolves path-style column ref", "status_id" in col_meta and "Error:" not in col_meta, col_meta)

    short_col_meta = meta_command(ws, "address_status/status_id", all=True)
    ok("meta resolves table/col exact path", "status_id" in short_col_meta and "Error:" not in short_col_meta, short_col_meta)

    dotted_col_meta = meta_command(ws, "address_status.status_id", all=True)
    ok("meta resolves dotted table.col ref", "status_id" in dotted_col_meta and "Error:" not in dotted_col_meta, dotted_col_meta)

    file_dotted_col_meta = meta_command(ws, "books.sqlite/address_status.status_id", all=True)
    ok("meta resolves file/table.col ref", "status_id" in file_dotted_col_meta and "Error:" not in file_dotted_col_meta, file_dotted_col_meta)

    print("\n[2] Read tools")
    ref_tables = find_command(ws, ref="books.sqlite/*:table")
    ok("find lists db tables from fs root", ".:dir/books.sqlite:db/address_status:table" in ref_tables and ".:dir/books.sqlite:db/order_status:table" in ref_tables, ref_tables)

    find_tables = find_command(ws, ref="books.sqlite/*:table")
    ok("find path scope returns source refs", ".:dir/books.sqlite:db/address_status:table" in find_tables and ".:dir/books.sqlite:db/order_status:table" in find_tables, find_tables)

    find_all_tables = find_command(ws, ref="*:table")
    ok("find *:table lists database tables only", "orders.csv/orders" not in find_all_tables, find_all_tables)

    find_csv_tables = find_command(ws, ref="*:csv_table")
    ok("find does not expose csv_table graph projections", "orders:csv_table" not in find_csv_tables, find_csv_tables)

    ref_cols = find_command(ws, ref="books.sqlite/*:table/*:col")
    ok("find displays source-rooted columns", ".:dir/books.sqlite:db/address_status:table/status_id:col" in ref_cols, ref_cols)

    source_col_meta = meta_command(ws, ".:dir/books.sqlite:db/address_status:table/status_id:col", all=True)
    ok("meta resolves source-rooted column ref", "Error:" not in source_col_meta and "status_id" in source_col_meta, source_col_meta)

    display_table_meta = meta_command(ws, "books.sqlite/address_status:table", all=True)
    ok("meta accepts typed table display ref", "address_status" in display_table_meta and "Error:" not in display_table_meta, display_table_meta)

    display_col_meta = meta_command(ws, "books.sqlite/address_status/status_id:col", all=True)
    ok("meta accepts labeled column display ref", "status_id" in display_col_meta and "Error:" not in display_col_meta, display_col_meta)

    typed_short_col_meta = meta_command(ws, "address_status/status_id:col", all=True)
    ok("meta accepts labeled table/col display ref", "status_id" in typed_short_col_meta and "Error:" not in typed_short_col_meta, typed_short_col_meta)

    query_out = query_command(ws, 'SELECT status_id, address_status FROM address_status ORDER BY status_id', "books.sqlite")
    ok("query reads sqlite through db_connect", "Active" in query_out and "Inactive" in query_out, query_out)

    query_ref_out = query_command(ws, 'SELECT COUNT(*) AS n FROM order_status', ref="books.sqlite:file:db")
    ok("query accepts db file refs", "3" in query_ref_out and "n" in query_ref_out, query_ref_out)

    csv_query_out = query_command(ws, 'SELECT status, SUM(CAST(amount AS REAL)) AS total FROM this GROUP BY status ORDER BY status', ref="orders.csv:file:csv:text")
    ok("query reads csv refs through temporary SQL table", "delivered" in csv_query_out and "37.5" in csv_query_out, csv_query_out)

    csv_avg_out = query_command(ws, "SELECT AVG(amount) AS avg_amount FROM this WHERE status = 'delivered'", ref="orders.csv:file:csv:text")
    ok("query treats blank csv numeric cells as NULL", "18.75" in csv_avg_out, csv_avg_out)

    csv_alias_out = query_command(ws, "SELECT COUNT(*) AS n FROM orders WHERE status = 'delivered'", ref="orders.csv:file:csv:text")
    ok("query exposes csv filename alias", "3" in csv_alias_out and "n" in csv_alias_out, csv_alias_out)

    json_query_out = query_command(ws, "SELECT name FROM this WHERE kind = 'order'", ref="records.json:file:json")
    ok("query reads json records refs", "Pending Delivery" in json_query_out, json_query_out)

    json_avg_out = query_command(ws, 'SELECT AVG(score) AS avg_score FROM this', ref="records.json:file:json")
    ok("query keeps json numeric fields numeric", "15.0" in json_avg_out, json_avg_out)

    workspace_query_out = query_command(
        ws,
        "SELECT o.order_id, s.status_value FROM orders o JOIN order_status s ON o.status = 'delivered' AND s.status_id = 3 ORDER BY o.order_id",
        ref=".",
    )
    ok("query workspace exposes csv and db aliases together", "101" in workspace_query_out and "Delivered" in workspace_query_out, workspace_query_out)

    workspace_query_error = query_command(ws, 'SELECT * FROM missing_table', ref=".")
    ok("query workspace error lists available tables", "Available tables in ref=\".\"" in workspace_query_error and "orders" in workspace_query_error, workspace_query_error)

    query_reject = query_command(ws, 'DELETE FROM address_status', "books.sqlite")
    ok("query rejects write sql", "只允许只读" in query_reject or "只允许 SELECT" in query_reject, query_reject)

    replace_function = query_command(
        ws,
        "SELECT replace(address_status, 'A', 'X') AS normalized FROM address_status ORDER BY status_id LIMIT 1",
        "books.sqlite",
    )
    ok("query allows SQLite replace function", "Xctive" in replace_function, replace_function)

    multi_statement = query_command(ws, "SELECT 1; DELETE FROM address_status", "books.sqlite")
    ok("query rejects mixed read/write statements", "只允许只读" in multi_statement, multi_statement)

    cypher_out = cypher_command(ws, "MATCH (t:table) RETURN t")
    ok("cypher returns table rows", "address_status [:table]" in cypher_out and "order_status [:table]" in cypher_out, cypher_out)

    table_meta = meta_command(ws, "books.sqlite/address_status", all=True)
    ok("table meta keeps non-relational schema facts", "row_count:" in table_meta and "primary_key:" in table_meta and "column_count:" not in table_meta, table_meta)
    ok("table meta hides redundant db context fields", all(s not in table_meta for s in [
        "path:",
        "db_name:",
        "db_path:",
        "table_name:",
    ]), table_meta)

    table_summary = meta_command(ws, "books.sqlite/address_status", property=["brief", "detail"])
    ok("table meta derives fallback brief/detail without edge-derived counts", "rows" in table_summary and "cols" not in table_summary, table_summary)

    column_meta = meta_command(ws, "books.sqlite/address_status/status_id", all=True)
    ok("column meta keeps useful column facts", "not_null:" in column_meta and "ref:" not in column_meta, column_meta)
    ok("column meta hides redundant context/type fields", all(s not in column_meta for s in [
        "path:",
        "db_name:",
        "db_path:",
        "table_name:",
        "col_type:",
    ]), column_meta)

    column_summary = meta_command(ws, "books.sqlite/address_status/status_id", property=["brief", "detail"])
    ok("column meta derives fallback brief/detail when absent", "INT" in column_summary or "default=" in column_summary, column_summary)

    column_stats = meta_command(ws, "books.sqlite/address_status/address_status", property=["cardinality", "sample", "topk"])
    ok("column meta computes runtime cardinality through db_connect", "cardinality: 2" in column_stats, column_stats)
    ok("column meta computes runtime sample/topk through db_connect", "Active" in column_stats and "Inactive" in column_stats and "topk:" in column_stats, column_stats)

    fk_ref = "books.sqlite--order_history.status_id->order_status.status_id"
    ws.cypher(
        "MATCH (n:fk) WHERE n._ref = $ref OR n.ref = $ref SET n += $props RETURN n",
        params={
            "ref": fk_ref,
            "props": {
            "db_name": "books.sqlite",
            "db_path": "books.sqlite",
            "from_table": "order_history",
            "from_column": "status_id",
            "to_table": "order_status",
            "to_column": "status_id",
            "relation_type": "foreign_key",
            "match_rate": 1.0,
            "total_count": 3,
            "violation_count": 0,
            },
        },
    )
    fk_meta = meta_command(ws, "books.sqlite/fks/order_history.status_id->order_status.status_id", all=True)
    ok("fk meta keeps useful counters", "match_rate: 1.0" in fk_meta and "total_count: 3" in fk_meta, fk_meta)
    ok("fk meta uses ordinary neighbor formatting", "[source]" not in fk_meta and "[target]" not in fk_meta, fk_meta)
    ok("fk meta hides redundant structural props", all(s not in fk_meta for s in [
        "db_name:",
        "db_path:",
        "from_table:",
        "from_column:",
        "to_table:",
        "to_column:",
        "relation_type:",
        "path:",
        "name:",
        "labels:",
    ]), fk_meta)

    grep_out = grep_command(ws, pattern="address status", ref="README.md", output_mode="content")
    ok("grep finds README content", "address status" in grep_out.lower(), grep_out)

    grep_ref_out = grep_command(ws, pattern="order status", ref="*:file:text", output_mode="content")
    ok(
        "grep keeps wildcard text scope and excludes binary siblings",
        "order status" in grep_ref_out.lower() and "binary.bin" not in grep_ref_out,
        grep_ref_out,
    )

    grep_missing = grep_command(ws, pattern="address", ref="missing.txt")
    ok("grep reports missing ref", "Ref does not exist" in grep_missing, grep_missing)

    read_out = read_command(ws, ref="README.md", start_line=1, end_line=3)
    ok("read returns line-numbered text through open_file", "README.md:L1-L3" in read_out and "1 | # Books" in read_out, read_out)

    read_ref_out = read_command(ws, ref="README.md:file:text", start_line=1, end_line=2)
    ok("read accepts file display refs", "README.md:L1-L2" in read_ref_out and "1 | # Books" in read_ref_out, read_ref_out)

    jd_root = jd_command(ws, ref="records.json", limit=10)
    ok(
        "jd browses JSON root through open_file",
        "key/index | value type | value info" in jd_root
        and "records | ARRAY | 3 items" in jd_root
        and "Open child: jd(ref=\"records.json#/<key-or-index>\")" in jd_root,
        jd_root,
    )

    jd_records = jd_command(ws, ref="records.json:file:json#/records", limit=1)
    ok(
        "jd accepts JSON VFS refs and paginates arrays",
        "array item keys:" in jd_records
        and "0 | DICT | 4 keys: id, name, kind, score" in jd_records
        and "Open child: jd(ref=\"records.json#/records/<key-or-index>\")" in jd_records,
        jd_records,
    )

    mixed_history = [
        ("find", {"ref": "books.sqlite/*:table"}, ref_tables),
        ("find", {"ref": "*", "query": "status"}, "books.sqlite/address_status:table"),
        ("meta", {"ref": "books.sqlite/address_status"}, table_meta),
    ]
    read_refs = get_meta_read(mixed_history)
    ok(
        "SQL guardrail ignores non-meta history when checking reads",
        "books.sqlite/address_status" in read_refs and "address_status" in read_refs,
        str(read_refs),
    )

    print("\n[3] update_meta")
    detail_text = (
        "Address status primary key\n\n"
        "status_id belongs to the address status domain only."
    )
    update_out = update_meta_command(
        ws,
        "books.sqlite/address_status/status_id",
        {"detail": detail_text},
    )
    ok("update_meta returns exact path ref", "OK books.sqlite/address_status/status_id:" in update_out, update_out)
    ok("update_meta returns written detail text", detail_text in update_out, update_out)

    other_detail = (
        "Order status primary key\n\n"
        "status_id belongs to the order status domain only."
    )
    update_meta_command(
        ws,
        "books.sqlite/order_status/status_id",
        {"detail": other_detail},
    )
    internal_ref_out = update_meta_command(
        ws,
        "books.sqlite--customer_address--status_id",
        {"brief": "Customer address status foreign key"},
    )
    ok("update_meta accepts internal db--table--col ref", "Error:" not in internal_ref_out, internal_ref_out)
    update_meta_command(
        ws,
        "books.sqlite/order_status/status_id",
        {"hints": ["old hint", "keep this"]},
    )
    update_meta_command(
        ws,
        "books.sqlite/order_status/status_id",
        {"hints": ["replacement hint"]},
    )

    address_meta = get_entity_meta(ws, "books.sqlite--address_status--status_id") or {}
    order_meta = get_entity_meta(ws, "books.sqlite--order_status--status_id") or {}
    customer_meta = get_entity_meta(ws, "books.sqlite--customer_address--status_id") or {}

    ok("address_status.status_id stores its own detail", address_meta.get("detail") == detail_text, str(address_meta.get("detail")))
    ok("order_status.status_id stores independent detail", order_meta.get("detail") == other_detail, str(order_meta.get("detail")))
    ok("update_meta replaces hints", order_meta.get("hints") == ["replacement hint"], str(order_meta.get("hints")))
    ok("internal ref update writes target metadata", customer_meta.get("brief") == "Customer address status foreign key", str(customer_meta.get("brief")))
    ok("customer_address.status_id remains untouched", customer_meta.get("detail") in (None, ""), str(customer_meta.get("detail")))

    print("\n[4] create_entity + find query")
    create_out = create_entity_command(
        ws,
        "status_id_domain:disambig",
        meta={
            "brief": "status_id ambiguity",
            "detail": "Distinguish address status from order status.",
        },
        edges=[
            {"ref": "books.sqlite/address_status/status_id"},
            {"ref": "books.sqlite/order_status/status_id"},
        ],
    )
    ok("create_entity creates disambig entity", "Created: status_id_domain" in create_out, create_out)
    ok("create_entity attaches path-ref edges", "books.sqlite/address_status/status_id" in create_out and "books.sqlite/order_status/status_id" in create_out, create_out)
    disambig_db_edges = ws.cypher(
        "MATCH (:db)--(x:disambig {name: 'status_id_domain'}) RETURN count(x) AS n"
    )
    ok(
        "create_entity gives disambig a direct database navigation edge",
        bool(disambig_db_edges and disambig_db_edges[0].get("n")),
        str(disambig_db_edges),
    )

    chunk_a = create_entity_command(
        ws,
        "0001:chunk",
        meta={
            "chunk_index": 1,
            "start_line": 1,
            "end_line": 2,
            "brief": "README chunk",
            "detail": "First README chunk.",
        },
        edges=[{"ref": "README.md"}],
    )
    chunk_b = create_entity_command(
        ws,
        "0001:chunk",
        meta={
            "chunk_index": 1,
            "start_line": 1,
            "end_line": 2,
            "brief": "Glossary chunk",
            "detail": "First glossary chunk.",
        },
        edges=[{"ref": "notes/glossary.txt"}],
    )
    ok("create_entity allows same chunk display name for different sources", "Created: 0001" in chunk_a and "Created: 0001" in chunk_b, chunk_a + "\n" + chunk_b)
    chunk_edges = ws.cypher(
        """
        MATCH (f:file)--(c:chunk {name: '0001'})
        RETURN f.path AS file_path, c.brief AS brief
        ORDER BY file_path, brief
        """
    )
    edge_pairs = {(row.get("file_path"), row.get("brief")) for row in chunk_edges}
    ok(
        "same-name chunks connect only to their own source files",
        ("README.md", "README chunk") in edge_pairs
        and ("notes/glossary.txt", "Glossary chunk") in edge_pairs
        and ("README.md", "Glossary chunk") not in edge_pairs
        and ("notes/glossary.txt", "README chunk") not in edge_pairs,
        str(edge_pairs),
    )

    create_entity_command(
        ws,
        "status_id:disambig",
        meta={"brief": "status_id disambiguation", "detail": "use this node when the bare name is ambiguous"},
        edges=[{"ref": "README.md"}],
    )
    bare_disambig = meta_command(ws, "status_id", property=["detail"])
    ok("bare ambiguous name resolves to unique disambig entity", "use this node when the bare name is ambiguous" in bare_disambig and "Error:" not in bare_disambig, bare_disambig)

    search_out = find_command(ws, ref="*:disambig", query="address status ambiguity")
    ok("find query finds created disambig entity", "status_id_domain" in search_out, search_out)
    find_search_out = find_command(ws, ref="*:disambig", query="address status ambiguity")
    ok("find searches within ref scope", "status_id_domain" in find_search_out, find_search_out)
    path_search = find_command(ws, ref="*:col", query="address status domain")
    ok(
        "find query returns source-rooted column refs",
        ".:dir/books.sqlite:db/address_status:table/status_id:col" in path_search,
        path_search,
    )

    create_entity_command(
        ws,
        "knowledge_rule:knowledge:convention",
        meta={"brief": "status id ambiguity", "detail": "shared status id ambiguity"},
        edges=[{"ref": "README.md"}],
    )
    create_entity_command(
        ws,
        "knowledge_case:knowledge:example",
        meta={"brief": "status id ambiguity", "detail": "shared status id ambiguity"},
        edges=[{"ref": "README.md"}],
    )
    knowledge_find = find_command(ws, ref=f"{os.path.basename(project)}::*:knowledge")
    rule_idx = knowledge_find.find("knowledge_rule:knowledge")
    example_idx = knowledge_find.find("knowledge_case:knowledge")
    ok("find orders abstract knowledge before examples", rule_idx != -1 and example_idx != -1 and rule_idx < example_idx, knowledge_find)

    knowledge_search = find_command(ws, ref="knowledge_rule:knowledge:convention", query="status id ambiguity")
    rule_search_idx = knowledge_search.find("knowledge_rule:knowledge:convention")
    ok("find query can target abstract knowledge by exact ref", "knowledge_rule:knowledge" in knowledge_search, knowledge_search)

    create_entity_command(
        ws,
        "knowledge_case_sparse:knowledge:example",
        meta={
            "brief": "sparse example",
            "question": "Which address status is active?",
            "evidence": "active means address_status = 'Active'",
            "predicted_sql": "SELECT address_status FROM address_status WHERE status_id = 1",
            "golden_sql": "SELECT address_status FROM address_status WHERE address_status = 'Active'",
            "mistake_summary": "used id instead of literal condition",
            "transfer_hint": "evidence literal should override guessed id mapping",
        },
        edges=[{"ref": "README.md"}],
    )
    sparse_meta = meta_command(ws, "knowledge_case_sparse:knowledge:example", property=["detail"])
    ok("knowledge example detail falls back to structured fields", "transfer_hint:" in sparse_meta and "golden_sql:" in sparse_meta, sparse_meta)

    normalized = normalize_knowledge_meta(
        "bird",
        ["knowledge", "example"],
        {
            "question": "Which address status is active?",
            "evidence": "active means address_status = 'Active'",
            "mistake_summary": "used id instead of literal condition",
            "transfer_hint": "literal evidence should dominate guessed surrogate ids",
            "golden_sql": "SELECT address_status FROM address_status WHERE address_status = 'Active'",
        },
    )
    ok("bird knowledge helper auto-derives brief", bool(normalized.get("brief")), str(normalized))
    ok("bird knowledge helper auto-derives detail", "mistake_summary:" in str(normalized.get("detail", "")), str(normalized))

    print("\n[5] add_edge")
    add_edge_out = add_edge_command(
        ws,
        [
            {
                "a": "books.sqlite/customer_address/status_id",
                "b": "status_id_domain:disambig",
            }
        ],
    )
    ok("add_edge accepts path ref endpoint", "已添加 1 条边" in add_edge_out, add_edge_out)

    disambig_meta = meta_command(ws, "status_id_domain", all=True)
    ok(
        "meta shows related columns after add_edge",
        disambig_meta.count("status_id:col") >= 3 and ".:dir/books.sqlite:db/customer_address:table/status_id:col" in disambig_meta,
        disambig_meta,
    )

    disambig_columns = meta_command(
        ws, "status_id_domain:disambig", neighbor_label="col", offset=0, limit=2
    )
    ok(
        "meta paginates source-rooted disambig column neighbors",
        "共 3 条邻接" in disambig_columns
        and ".:dir/books.sqlite:db/" in disambig_columns
        and "offset=2" in disambig_columns,
        disambig_columns,
    )

    rel_meta = meta_command(ws, "books.sqlite/order_history.status_id->books.sqlite/order_status.status_id", all=True)
    ok("meta accepts path-style relation ref with db prefixes", "match_rate: 1.0" in rel_meta and "Error:" not in rel_meta, rel_meta)

    bare_rel_meta = meta_command(ws, "order_history.status_id->order_status.status_id", all=True)
    ok("meta prefers labeled relation entity for bare relation ref", "order_history.status_id->order_status.status_id" in bare_rel_meta and "Error:" not in bare_rel_meta, bare_rel_meta)

    fk_list = find_command(ws, ref="books.sqlite:db/*:fk")
    ok("find lists fk entities from db entrypoint", "order_history.status_id->order_status.status_id" in fk_list, fk_list)

    fk_search = find_command(ws, ref="*:fk", query="order_history status_id order_status foreign key")
    ok("find query can find fk entities by name tokens", "order_history.status_id->order_status.status_id" in fk_search, fk_search)

    # Relation entities use the same ordinary name:tag contract as all other
    # entities; member columns are represented only by graph edges.
    create_entity_command(
        ws,
        "value_domain_test:overlap",
        meta={"brief": "status id value-domain overlap"},
        edges=[
            {"ref": "books.sqlite/address_status/status_id"},
            {"ref": "books.sqlite/order_status/status_id"},
        ],
    )
    overlap_meta = meta_command(
        ws, "value_domain_test:overlap",
        property=["brief"],
    )
    ok(
        "overlap uses ordinary entity ref and round-trips to meta",
        "status id value-domain overlap" in overlap_meta and "Error:" not in overlap_meta,
        overlap_meta,
    )

    print("\n[6] delete")
    delete_out = delete_command(ws, "books.sqlite/address_status/status_id")
    ok("delete accepts path-style column ref", "status_id" in delete_out and "已删除" in delete_out, delete_out)

    deleted_meta = meta_command(ws, "books.sqlite/address_status/status_id", all=True)
    ok("deleted path ref falls back to virtual source entity", "Error:" not in deleted_meta and "Address status primary key" not in deleted_meta, deleted_meta)

    delete_disambig = delete_command(ws, "status_id_domain")
    ok("delete removes created disambig entity", "status_id_domain" in delete_disambig, delete_disambig)

    print("\n[7] Negative cases")
    bad_meta = meta_command(ws, "books.sqlite/no_such_table/no_such_col", all=True)
    ok("meta reports missing entity", "Error:" in bad_meta, bad_meta)

    bad_update = update_meta_command(ws, "books.sqlite/order_status/status_id", {"sample": "x"})
    ok("update_meta rejects unsupported field", "不允许修改" in bad_update, bad_update)

    bad_edge = add_edge_command(ws, [{"a": "", "b": "status_id_domain"}])
    ok("add_edge rejects missing endpoint", "缺少必填字段" in bad_edge or "没有有效的边可添加" in bad_edge, bad_edge)

    print("\n[8] Agent tool argument parsing")
    ok("agent parser accepts pre-parsed dict arguments", PontusAgent._parse_args({"ref": "x"}) == {"ref": "x"})
    ok(
        "agent parser repairs literal newlines in JSON strings",
        PontusAgent._parse_args('{"ref":"x","fields":{"detail":"a\nb"}}').get("fields", {}).get("detail") == "a\nb",
    )
    ok(
        "agent parser repairs missing closing object braces",
        PontusAgent._parse_args('{"ref":"x","fields":{"detail":"ok"}').get("ref") == "x",
    )
    ok(
        "agent parser repairs unescaped quotes in JSON string values",
        PontusAgent._parse_args('{"ref":"x","fields":{"detail":"table（表名\"Examination\"）"}}')
        .get("fields", {})
        .get("detail")
        == 'table（表名"Examination"）',
    )
    ok(
        "agent parser keeps colon after quoted field names inside values",
        PontusAgent._parse_args('{"ref":"x","fields":{"detail":"字段\"task_id\": STR"}}')
        .get("fields", {})
        .get("detail")
        == '字段"task_id": STR',
    )

    cleanup_test_graph(ws)

    print(f"\nResult: {passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
