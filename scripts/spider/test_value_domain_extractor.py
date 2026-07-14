import extractor.db_value_domain as value_domain_module
from extractor.db_value_domain import (
    _domain_summary,
    _evenly_spaced_members,
    _load_materialized_comparison_columns,
    _minimum_domain_overlap,
    _open_value_database_with_retry,
    _relation_is_inaccessible,
    _semantic_profile,
    _weak_key_alias,
)
from extractor.utils.online_value_domains import OnlineValueDomain, OnlineValueDomainConfig, ValueColumn
from extractor.utils.semantic_domain import classify_semantic_domain


def _value_column(ref: str, name: str, values: set[int], data_type: str = "NUMBER") -> ValueColumn:
    profile = classify_semantic_domain(name, data_type)
    return ValueColumn(
        ref=ref,
        values=frozenset(values),
        bucket="PUBLIC",
        metadata={
            "entity_name": ref,
            "column": name,
            "schema_name": "PUBLIC",
            "data_type": data_type,
            "semantic_profile": profile,
        },
    )


def test_numeric_local_ordinal_does_not_merge_with_identifier():
    flight_id = _value_column("flight", "flight_id", set(range(1, 101)))
    boarding_no = _value_column("boarding", "boarding_no", set(range(1, 11)))

    assert _weak_key_alias(flight_id, boarding_no, set())


def test_different_named_keys_use_value_threshold_instead_of_hard_rejection():
    customer = _value_column("customer", "customer_id", {1, 2, 3})
    product = _value_column("product", "product_id", {1, 2, 3})

    assert _minimum_domain_overlap(customer, product) == 0.3


def test_weak_key_to_unknown_alias_requires_high_jaccard():
    constructor = _value_column("constructor", "constructor_id", set(range(1, 101)))
    round_number = _value_column("round", "round", set(range(1, 12)))

    assert _weak_key_alias(constructor, round_number, set())


def test_high_jaccard_key_to_unknown_alias_is_retained():
    player = _value_column("player", "player_id", set(range(1, 101)))
    striker = _value_column("striker", "striker", set(range(1, 96)))

    assert not _weak_key_alias(player, striker, set())


def test_generic_column_uses_table_as_entity_context():
    profile = _semantic_profile({
        "column": "id",
        "table_name": "USERS",
        "data_type": "NUMBER",
        "sample": [],
        "domain_profile": {},
    })

    assert "user" in profile["entity_tokens"]


def test_value_domain_summary_links_comparison_units_not_physical_expansion():
    logical = _value_column("logical:patent", "patent_id", {1, 2, 3})
    logical.metadata["domain_members"] = [
        {"entity_name": "physical:2020"},
        {"entity_name": "physical:2021"},
    ]
    standalone = _value_column("physical:citation", "patent_id", {2, 3})
    domain = OnlineValueDomain(
        domain_id=0,
        bucket="PUBLIC",
        members=[logical, standalone],
        union_values={1, 2, 3},
        anchors=[logical],
    )

    summary = _domain_summary("PATENTSVIEW", domain, OnlineValueDomainConfig())

    assert summary["member_refs"] == ["logical:patent", "physical:citation"]
    assert summary["member_count"] == 2


def test_logical_member_sampling_covers_whole_partition_range():
    members = [{"entity_name": f"partition:{year}"} for year in range(2000, 2020)]

    sampled = _evenly_spaced_members(members, 4)

    assert len(sampled) == 4
    assert sampled[0]["entity_name"] == "partition:2000"
    assert sampled[-1]["entity_name"] == "partition:2019"


def test_only_relation_level_access_errors_suppress_later_columns():
    assert _relation_is_inaccessible(Exception("Object 'T' does not exist or not authorized."))
    assert not _relation_is_inaccessible(Exception("invalid identifier 'MISSING_COLUMN'"))


def test_value_database_connection_retries_transient_failures():
    attempts = []

    def connect(*, readonly):
        attempts.append(readonly)
        if len(attempts) < 3:
            raise OSError("temporary network failure")
        return "connection"

    original_sleep = value_domain_module.time.sleep
    value_domain_module.time.sleep = lambda _seconds: None
    try:
        assert _open_value_database_with_retry(connect, attempts=3) == "connection"
    finally:
        value_domain_module.time.sleep = original_sleep
    assert attempts == [True, True, True]


class _Workspace:
    def cypher(self, _query, params=None):
        if params == {"db_ref": "DB"}:
            return [{
                "l": {"_ref": "logical:id", "role": "id"},
                "g": {"_ref": "group:years", "name": "year shards", "schema_name": "PUBLIC"},
            }]
        assert params == {"logical_ref": "logical:id"}
        return [
            {"member_ref": "physical:2020"},
            {"member_ref": "physical:2021"},
        ]


def test_materialized_logical_columns_replace_their_physical_members():
    physical = [
        {
            "entity_name": ref,
            "db_ref": "DB",
            "table": table,
            "table_ref": table,
            "table_name": table,
            "schema_name": "PUBLIC",
            "column": "id",
            "column_ref": ref,
            "data_type": "NUMBER",
            "sample": [],
            "topk": [],
        }
        for ref, table in (
            ("physical:2020", "table:2020"),
            ("physical:2021", "table:2021"),
            ("standalone", "table:other"),
        )
    ]

    result = _load_materialized_comparison_columns(_Workspace(), "DB", physical)

    assert {column["entity_name"] for column in result} == {"logical:id", "standalone"}
    logical = next(column for column in result if column["entity_name"] == "logical:id")
    assert {member["entity_name"] for member in logical["domain_members"]} == {
        "physical:2020", "physical:2021",
    }
