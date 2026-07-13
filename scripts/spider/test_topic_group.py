from explorer.topic_group import (
    LARGE_SCHEMA_MIN_LOGICAL_UNITS,
    _completion_prompt,
    _uncovered_large_schema_units,
)


class FakeWorkspace:
    def cypher(self, query, params=None):
        assert "NOT (unit)--(:topic)" in query
        assert params == {"minimum_units": LARGE_SCHEMA_MIN_LOGICAL_UNITS}
        return [
            {"ref": "DB--S--TABLE", "schema_name": "S", "kind": "standalone_table"},
            {"ref": "DB--S--GROUP", "schema_name": "S", "kind": "table_group"},
        ]


def test_large_schema_topic_coverage_reports_uncovered_units():
    assert _uncovered_large_schema_units(FakeWorkspace()) == [
        "DB--S--TABLE (S/standalone_table)",
        "DB--S--GROUP (S/table_group)",
    ]


def test_topic_completion_prompt_names_missing_entities():
    prompt = _completion_prompt(["DB--S--TABLE (S/standalone_table)"], 1)
    assert "DB--S--TABLE" in prompt
    assert "add_edge" in prompt
