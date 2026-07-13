from explorer.utils.value_domain_candidates import (
    build_value_domain_candidates,
    candidate_batches,
)
from explorer.value_domain_review import render_candidate_prompt


class FakeWorkspace:
    def cypher(self, query, params=None):
        assert "value_domain" in query
        assert params == {"statuses": ["pending_review"]}
        domain = {
            "_ref": "AIRLINES--value_domain--AIRLINES--abc",
            "name": "value_domain[AIRLINES:airport_code]",
            "schema_name": "AIRLINES",
            "review_status": "pending_review",
            "union_cardinality": 104,
            "semantic_roles": '{"identifier": 2}',
            "overlap_metric": "intersection_over_min_cardinality",
            "overlap_threshold": 0.5,
            "min_anchor_support": 0.75,
            "extraction_evidence": [{"union_overlap": 1.0}],
        }
        return [
            {
                "domain": domain,
                "member": {
                    "_ref": "AIRLINES--AIRLINES--AIRPORTS_DATA--airport_code",
                    "name": "airport_code",
                    "schema_name": "AIRLINES",
                    "data_type": "TEXT",
                    "labels": ["col", "TEXT", "standalone"],
                    "sample": ["SVO", "LED"],
                },
                "direct_tables": ["AIRPORTS_DATA"],
                "physical_members": [],
            },
            {
                "domain": domain,
                "member": {
                    "_ref": "AIRLINES--group--airport--logical_col--arrival_airport",
                    "name": "logical_col[arrival_airport]",
                    "role": "arrival_airport",
                    "schema_name": "AIRLINES",
                    "member_count": 3,
                    "labels": ["logical_col", "grouped"],
                },
                "direct_tables": [],
                "physical_members": [
                    {
                        "ref": "AIRLINES--AIRLINES--FLIGHTS--arrival_airport",
                        "table_name": "FLIGHTS",
                        "column_name": "arrival_airport",
                    }
                ],
            },
        ]


def test_build_value_domain_candidates_keeps_physical_and_logical_members():
    candidates = build_value_domain_candidates(FakeWorkspace())
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.review_status == "pending_review"
    assert [member.kind for member in candidate.members] == ["col", "logical_col"]
    assert candidate.members[1].physical_members == (
        "AIRLINES--AIRLINES--FLIGHTS--arrival_airport",
    )


def test_render_candidate_prompt_requires_domain_review_and_shows_members():
    candidate = build_value_domain_candidates(FakeWorkspace())[0]
    rendered = render_candidate_prompt(
        [candidate],
        batch_index=1,
        batch_count=1,
        start_index=1,
        total_count=1,
    )
    assert "review_status" in rendered
    assert "logical_col--arrival_airport" in rendered
    assert "physical member refs" in rendered


def test_candidate_batches_rejects_invalid_size():
    try:
        candidate_batches([], 0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected ValueError")
