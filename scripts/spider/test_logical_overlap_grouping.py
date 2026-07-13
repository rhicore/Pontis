from dataclasses import replace

from extractor.db_column_overlap import _group_overlap_table_refs
from extractor.utils.overlap_evidence import _detect_name_overlaps, _group_pair_overlaps
from extractor.utils.overlap_group_policy import apply_overlap_group_policy
from extractor.utils.overlap_options import OverlapOptions


def _side(ref: str, role: str, member_count: int = 1) -> dict:
    return {
        "domain_ref": ref,
        "domain_unit": f"group:{role}",
        "domain_role": role,
        "domain_member_count": member_count,
        "members": [
            {
                "ref": f"physical:{role}:{index}",
                "table": f"table:{role}:{index}",
                "table_ref": f"table:{role}:{index}",
                "column": role,
                "type": "TEXT",
            }
            for index in range(member_count)
        ],
    }


def _pair(left: dict, right: dict, score: float) -> dict:
    return {
        "from_ref": left["domain_ref"],
        "to_ref": right["domain_ref"],
        "from_table": left["domain_unit"],
        "to_table": right["domain_unit"],
        "from_column": left["domain_role"],
        "to_column": right["domain_role"],
        "from_type": "TEXT",
        "to_type": "TEXT",
        "domain_sides": [left, right],
        "sources": ["value_domain"],
        "stats": {"overlap_coefficient": score},
    }


def test_logical_domains_participate_in_multi_column_grouping():
    left = _side("logical:a", "a", 2)
    middle = _side("logical:b", "b", 1)
    right = _side("logical:c", "c", 2)

    groups = _group_pair_overlaps([
        _pair(left, middle, 0.9),
        _pair(middle, right, 0.8),
    ])

    assert len(groups) == 1
    group = groups[0]
    assert {column["ref"] for column in group["columns"]} == {"logical:a", "logical:b", "logical:c"}
    assert {side["domain_ref"] for side in group["domain_sides"]} == {"logical:a", "logical:b", "logical:c"}
    assert group["stats"]["column_count"] == 3
    assert group["stats"]["pair_count"] == 2
    assert len(_group_overlap_table_refs(group["columns"], group["domain_sides"])) == 5


def test_two_logical_domains_remain_a_group_entity():
    left = _side("logical:a", "a")
    right = _side("logical:b", "b")

    groups = _group_pair_overlaps([_pair(left, right, 0.9)])

    assert len(groups) == 1
    assert "columns" in groups[0]
    assert len(groups[0]["columns"]) == 2
    assert len(groups[0]["domain_sides"]) == 2


def test_two_physical_columns_keep_pair_entity_shape():
    pair = {
        "from_ref": "physical:a",
        "to_ref": "physical:b",
        "from_table": "table:a",
        "to_table": "table:b",
        "from_column": "a",
        "to_column": "b",
        "from_type": "TEXT",
        "to_type": "TEXT",
        "stats": {"overlap_coefficient": 0.9},
    }

    groups = _group_pair_overlaps([pair])

    assert groups == [pair]


def test_name_groups_use_logical_columns_and_keep_physical_members():
    columns = []
    for logical_ref, table in (("logical:a", "table:a"), ("logical:b", "table:b")):
        member = {
            "entity_name": f"physical:{logical_ref}",
            "table": table,
            "table_ref": table,
            "table_name": table,
            "column": "patent_id",
            "data_type": "TEXT",
        }
        columns.append({
            "entity_name": logical_ref,
            "table": f"group:{table}",
            "table_ref": f"group:{table}",
            "table_name": f"group:{table}",
            "column": "patent_id",
            "data_type": "TEXT",
            "domain_unit": f"group:{table}",
            "domain_role": "patent_id",
            "domain_members": [member],
        })

    groups = _detect_name_overlaps(columns, {})
    patent_group = next(group for group in groups if group["stats"]["name_tokens"] == ["patent"])

    assert {column["ref"] for column in patent_group["columns"]} == {"logical:a", "logical:b"}
    assert {side["domain_ref"] for side in patent_group["domain_sides"]} == {"logical:a", "logical:b"}
    assert {side["members"][0]["ref"] for side in patent_group["domain_sides"]} == {
        "physical:logical:a",
        "physical:logical:b",
    }


def _policy_options() -> OverlapOptions:
    return replace(
        OverlapOptions(),
        group_policy_enabled=True,
        group_drop_name_only=True,
        group_drop_local_ordinal=True,
        group_drop_low_overlap_text=True,
        group_low_overlap_text_threshold=0.1,
        group_auto_accept_min_overlap=0.1,
    )


def _policy_group(names: list[str], score: float, sources: list[str] | None = None) -> dict:
    return {
        "columns": [
            {"ref": f"logical:{index}", "table": f"table:{index}", "column": name, "type": "TEXT"}
            for index, name in enumerate(names)
        ],
        "sources": sources or ["value_domain"],
        "stats": {"min_overlap_coefficient": score, "max_overlap_coefficient": score},
    }


def test_group_policy_rejects_name_only_and_static_noise():
    groups = [
        _policy_group(["patent_id", "patent_id"], 1.0, ["name_keyword"]),
        _policy_group(["sequence", "sequence"], 1.0),
        _policy_group(["title", "field_title"], 0.05),
    ]

    retained, stats = apply_overlap_group_policy(groups, _policy_options())

    assert retained == []
    assert stats["rejected_name_only"] == 1
    assert stats["rejected_local_ordinal"] == 1
    assert stats["rejected_low_overlap_text"] == 1


def test_group_policy_routes_clear_keys_without_ai():
    groups = [
        _policy_group(["patent_id", "patent_id", "patent_id", "patent_id", "citation_id"], 0.02),
        _policy_group(["id", "organization_id"], 0.2),
    ]

    retained, stats = apply_overlap_group_policy(groups, _policy_options())

    assert [group["review_status"] for group in retained] == ["auto_accept", "ai_review"]
    assert stats["auto_accept"] == 1
    assert stats["ai_review"] == 1
