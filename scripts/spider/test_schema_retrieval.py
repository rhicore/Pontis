from scripts.spider.evaluate_schema_retrieval import _staged_hit, extract_golden_refs


def _metadata():
    columns = []
    for table, names in {
        "ORDERS": ["order_id", "customer_id", "amount"],
        "CUSTOMERS": ["customer_id", "name"],
    }.items():
        for name in names:
            columns.append({
                "table_ref": f"SHOP--PUBLIC--{table}",
                "table_name": table,
                "schema_name": "PUBLIC",
                "column": name,
            })
    return {"status": "ok", "columns": columns}


def test_extract_golden_refs_resolves_tables_aliases_and_columns():
    refs, unresolved = extract_golden_refs(
        """
        SELECT c.name, SUM(o.amount)
        FROM SHOP.PUBLIC.ORDERS AS o
        JOIN SHOP.PUBLIC.CUSTOMERS AS c
          ON o.customer_id = c.customer_id
        GROUP BY c.name
        """,
        _metadata(),
    )
    assert unresolved == []
    assert refs == {
        "SHOP--PUBLIC--ORDERS",
        "SHOP--PUBLIC--CUSTOMERS",
        "SHOP--PUBLIC--ORDERS--amount",
        "SHOP--PUBLIC--ORDERS--customer_id",
        "SHOP--PUBLIC--CUSTOMERS--customer_id",
        "SHOP--PUBLIC--CUSTOMERS--name",
    }


def test_extract_golden_refs_follows_physical_columns_inside_cte():
    refs, unresolved = extract_golden_refs(
        """
        WITH totals AS (
          SELECT customer_id, SUM(amount) AS total
          FROM PUBLIC.ORDERS
          GROUP BY customer_id
        )
        SELECT total FROM totals
        """,
        _metadata(),
    )
    assert "SHOP--PUBLIC--ORDERS--customer_id" in refs
    assert "SHOP--PUBLIC--ORDERS--amount" in refs
    assert all("total" not in item for item in unresolved)


def test_staged_recall_expands_columns_after_parent_table_hit():
    golden = [
        {"ref": "DB--S--T", "kind": "table", "rank": 3, "parent_table_ref": None, "navigation_rank": 2},
        {"ref": "DB--S--T--id", "kind": "col", "rank": None, "parent_table_ref": "DB--S--T", "navigation_rank": 2},
    ]
    assert _staged_hit(golden[1], 5, golden)
    assert not _staged_hit(golden[1], 1, golden)
