import extractor.utils.column_domain_entities as entities
from extractor.db_column_domain import _online_candidate, _pairwise_candidate


class _Workspace:
    def __init__(self, existing=False):
        self.existing = existing

    def cypher(self, query, params=None):
        return [{"d": {"_ref": params["ref"]}}] if self.existing else []


def test_identity_depends_on_members_not_strategy_or_direction():
    left = entities.column_domain_ref("DB", ["a", "b"])
    right = entities.column_domain_ref("DB", ["b", "a"])
    assert left == right


def test_upsert_preserves_human_review_fields():
    calls = []
    original = entities.write_project_cypher
    entities.write_project_cypher = (
        lambda workspace, query, params=None: calls.append((query, params)) or []
    )
    try:
        created = entities._upsert_domain(
            _Workspace(existing=True),
            ref="DB--column_domain--abc",
            name="column_domain_abc",
            metadata={
                "extraction_strategy": "pairwise_filter",
                "review_status": "pending_review",
                "brief": "extractor default",
                "stats": {"overlap_coefficient": 1.0},
            },
        )
    finally:
        entities.write_project_cypher = original

    assert not created
    _query, params = calls[0]
    assert "review_status" in params["props"]
    assert "review_status" not in params["refresh_props"]
    assert "brief" not in params["refresh_props"]
    assert params["refresh_props"]["extraction_strategy"] == "pairwise_filter"


def test_pairwise_and_online_strategies_share_candidate_shape():
    pairwise = _pairwise_candidate({
        "from_ref": "a",
        "to_ref": "b",
        "sources": ["value_overlap"],
        "stats": {"overlap_coefficient": 0.9},
    }, {"value_match_method": "sql"})
    online = _online_candidate({
        "_ref": "legacy",
        "name": "legacy",
        "db_ref": "DB",
        "schema_ref": "schema",
        "member_refs": ["a", "b"],
        "union_cardinality": 10,
        "review_status": "pending_review",
    })

    assert pairwise["member_refs"] == ["a", "b"]
    assert online["member_refs"] == ["a", "b"]
    assert pairwise["metadata"]["extraction_strategy"] == "pairwise_filter"
    assert online["metadata"]["extraction_strategy"] == "online_clustering"
