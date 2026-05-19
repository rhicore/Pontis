"""Tool layer regression tests.

Coverage:
- path/file ref resolution for file / table / column entities
- read tools: glob / meta / search / query / cypher / grep / bash
- write tools: update_meta / create_entity / add_edge / delete
- negative cases for common agent mistakes

Usage: python3 scripts/tool/test_tools.py
"""

import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from storage.workspace import Workspace
from agent.agent import PontusAgent
from agent.guardrail.sql_utils import get_meta_read
from extractor.modules.utils.refs import get_entity_meta
from tool.DB_query.tool import query_command
from tool.FS_grep.tool import grep_command
from tool.SH_bash.tool import bash_command
from tool.add_edge.tool import add_edge_command
from tool.create_entity.tool import create_entity_command
from tool.cypher.tool import cypher_command
from tool.delete.tool import delete_command
from tool.glob.tool import glob_command
from tool.utils.knowledge_meta import normalize_knowledge_meta
from tool.meta.tool import meta_command
from tool.search.tool import search_command
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
            "Use it to test grep and bash tools.\n"
        )

    os.makedirs(os.path.join(tmp, "notes"), exist_ok=True)
    with open(os.path.join(tmp, "notes", "glossary.txt"), "w", encoding="utf-8") as fh:
        fh.write("address status domain\norder status domain\n")

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
    ws.cypher(
        "MATCH (n) "
        "WHERE n.path = 'books.sqlite' "
        "OR n._ref STARTS WITH 'books.sqlite--' "
        "OR n.ref STARTS WITH 'books.sqlite--' "
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
            ]
        },
    )


def main():
    project = make_books_project()
    ws = Workspace(project_path=project)
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
    glob_tables = glob_command(ws, "books.sqlite/*:table")
    ok("glob lists db tables", "books.sqlite/address_status" in glob_tables and "books.sqlite/order_status" in glob_tables, glob_tables)

    glob_cols = glob_command(ws, "books.sqlite/*:table/*:col")
    ok("glob lists path-style columns", "books.sqlite/address_status/status_id" in glob_cols, glob_cols)

    display_table_meta = meta_command(ws, "books.sqlite/address_status:table", all=True)
    ok("meta accepts glob-style table display ref", "address_status" in display_table_meta and "Error:" not in display_table_meta, display_table_meta)

    display_col_meta = meta_command(ws, "books.sqlite/address_status/status_id:INTEGER:col", all=True)
    ok("meta accepts glob-style column display ref", "status_id" in display_col_meta and "Error:" not in display_col_meta, display_col_meta)

    typed_short_col_meta = meta_command(ws, "address_status/status_id:INTEGER:col", all=True)
    ok("meta accepts typed table/col display ref", "status_id" in typed_short_col_meta and "Error:" not in typed_short_col_meta, typed_short_col_meta)

    query_out = query_command(ws, 'SELECT status_id, address_status FROM address_status ORDER BY status_id', "books.sqlite")
    ok("query reads sqlite through db_connect", "Active" in query_out and "Inactive" in query_out, query_out)

    query_reject = query_command(ws, 'DELETE FROM address_status', "books.sqlite")
    ok("query rejects write sql", "只允许只读" in query_reject or "只允许 SELECT" in query_reject, query_reject)

    cypher_out = cypher_command(ws, "MATCH (t:table) RETURN t")
    ok("cypher returns table rows", "address_status [:table]" in cypher_out and "order_status [:table]" in cypher_out, cypher_out)

    table_meta = meta_command(ws, "books.sqlite/address_status", all=True)
    ok("table meta keeps useful schema facts", "column_count:" in table_meta and "primary_key:" in table_meta, table_meta)
    ok("table meta hides redundant db context fields", all(s not in table_meta for s in [
        "path:",
        "db_name:",
        "db_path:",
        "table_name:",
    ]), table_meta)

    table_summary = meta_command(ws, "books.sqlite/address_status", property=["brief", "detail"])
    ok("table meta derives fallback brief/detail when absent", "rows" in table_summary and "cols" in table_summary, table_summary)

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

    grep_out = grep_command(ws, pattern="address status", path="README.md", output_mode="content")
    ok("grep finds README content", "address status" in grep_out.lower(), grep_out)

    grep_missing = grep_command(ws, pattern="address", path="missing.txt")
    ok("grep reports missing path", "Path does not exist" in grep_missing, grep_missing)

    bash_out = bash_command("printf 'tool-bash-ok'", cwd=project)
    ok("bash executes command", "tool-bash-ok" in bash_out, bash_out)

    mixed_history = [
        ("glob", {"ref": "books.sqlite/*:table"}, glob_tables),
        ("search", {"ref": "*", "query": "status"}, "books.sqlite/address_status:table"),
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

    address_meta = get_entity_meta(ws, "books.sqlite--address_status--status_id") or {}
    order_meta = get_entity_meta(ws, "books.sqlite--order_status--status_id") or {}
    customer_meta = get_entity_meta(ws, "books.sqlite--customer_address--status_id") or {}

    ok("address_status.status_id stores its own detail", address_meta.get("detail") == detail_text, str(address_meta.get("detail")))
    ok("order_status.status_id stores independent detail", order_meta.get("detail") == other_detail, str(order_meta.get("detail")))
    ok("internal ref update writes target metadata", customer_meta.get("brief") == "Customer address status foreign key", str(customer_meta.get("brief")))
    ok("customer_address.status_id remains untouched", customer_meta.get("detail") in (None, ""), str(customer_meta.get("detail")))

    print("\n[4] create_entity + search")
    create_out = create_entity_command(
        ws,
        "status_id_domain:disambig",
        meta={
            "brief": "status_id ambiguity",
            "detail": "Distinguish address status from order status.",
        },
        edges=[
            {"a": "books.sqlite/address_status/status_id", "b": "status_id_domain:disambig"},
            {"a": "books.sqlite/order_status/status_id", "b": "status_id_domain:disambig"},
        ],
    )
    ok("create_entity creates disambig entity", "Created: status_id_domain" in create_out, create_out)
    ok("create_entity attaches path-ref edges", "books.sqlite/address_status/status_id" in create_out and "books.sqlite/order_status/status_id" in create_out, create_out)

    create_entity_command(
        ws,
        "status_id:disambig",
        meta={"brief": "status_id disambiguation", "detail": "use this node when the bare name is ambiguous"},
    )
    bare_disambig = meta_command(ws, "status_id", property=["detail"])
    ok("bare ambiguous name resolves to unique disambig entity", "use this node when the bare name is ambiguous" in bare_disambig and "Error:" not in bare_disambig, bare_disambig)

    search_out = search_command(ws, "*:disambig", "address status ambiguity")
    ok("search finds created disambig entity", "status_id_domain" in search_out, search_out)
    path_search = search_command(ws, "*:col", "address status domain")
    ok("search shows copyable path-style refs", "books.sqlite/" in path_search, path_search)

    create_entity_command(
        ws,
        "knowledge_rule:knowledge:convention",
        meta={"brief": "status id ambiguity", "detail": "shared status id ambiguity"},
    )
    create_entity_command(
        ws,
        "knowledge_case:knowledge:example",
        meta={"brief": "status id ambiguity", "detail": "shared status id ambiguity"},
    )
    knowledge_glob = glob_command(ws, "*:knowledge")
    rule_idx = knowledge_glob.find("knowledge_rule:knowledge:convention")
    example_idx = knowledge_glob.find("knowledge_case:knowledge:example")
    ok("glob orders abstract knowledge before examples", rule_idx != -1 and example_idx != -1 and rule_idx < example_idx, knowledge_glob)

    knowledge_search = search_command(ws, "knowledge_rule:knowledge:convention", "status id ambiguity")
    rule_search_idx = knowledge_search.find("knowledge_rule:knowledge:convention")
    ok("search can target abstract knowledge by exact ref", rule_search_idx != -1, knowledge_search)

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

    bird_ws = Workspace(active_projects=["bird"])
    create_entity_command(
        bird_ws,
        "placeholder_rule:knowledge:convention",
        meta={
            "brief": "-",
            "detail": "...",
            "transfer_hint": "use row-level filtering for explicit evidence conditions",
            "mistake_summary": "wrapped a direct condition into NOT EXISTS",
        },
    )
    placeholder_search = search_command(bird_ws, "bird::placeholder_rule:knowledge:convention", "row-level filtering explicit evidence condition")
    ok("search indexes normalized bird knowledge instead of placeholder brief/detail", "placeholder_rule:knowledge:convention" in placeholder_search, placeholder_search)
    placeholder_glob = glob_command(bird_ws, "bird::placeholder_rule:knowledge:convention")
    ok("glob shows normalized bird knowledge info instead of placeholder text", "use row-level filtering" in placeholder_glob or "wrapped a direct condition" in placeholder_glob, placeholder_glob)

    create_entity_command(
        bird_ws,
        "evidence_literal_test:knowledge:term",
        meta={
            "brief": "证据字面条件值优先于数据库采样值",
            "detail": "测试 knowledge base label fallback。",
        },
    )
    relaxed_knowledge_meta = meta_command(bird_ws, "evidence_literal_test:knowledge:convention", property=["brief"])
    ok("meta tolerates knowledge sublabel mismatch when base knowledge name is unique", "证据字面条件值优先于数据库采样值" in relaxed_knowledge_meta and "Error:" not in relaxed_knowledge_meta, relaxed_knowledge_meta)

    delete_command(bird_ws, "bird::placeholder_rule:knowledge:convention")
    delete_command(bird_ws, "bird::evidence_literal_test:knowledge:term")

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
        disambig_meta.count("status_id:col:INT") == 3 and "customer_address/status_id" not in disambig_meta,
        disambig_meta,
    )

    rel_meta = meta_command(ws, "books.sqlite/order_history.status_id->books.sqlite/order_status.status_id", all=True)
    ok("meta accepts path-style relation ref with db prefixes", "order_history.status_id->order_status.status_id" in rel_meta and "Error:" not in rel_meta, rel_meta)

    malformed_rel_meta = meta_command(ws, "books.sqlite/order_history.order_history.status_id->books.sqlite/order_status.status_id", all=True)
    ok("meta tolerates malformed relation endpoint with duplicated table token", "order_history.status_id->order_status.status_id" in malformed_rel_meta and "Error:" not in malformed_rel_meta, malformed_rel_meta)

    bare_rel_meta = meta_command(ws, "order_history.status_id->order_status.status_id", all=True)
    ok("meta prefers labeled relation entity for bare relation ref", "order_history.status_id->order_status.status_id" in bare_rel_meta and "Error:" not in bare_rel_meta, bare_rel_meta)

    fk_search = search_command(ws, "*:fk", "order_history status_id order_status foreign key")
    ok("search can find fk entities by name tokens", "order_history.status_id->order_status.status_id" in fk_search, fk_search)

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

    cleanup_test_graph(ws)

    print(f"\nResult: {passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
