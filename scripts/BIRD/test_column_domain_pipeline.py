"""BIRD column-domain pipeline regressions."""

from extractor.utils.overlap_options import _resolve_options
from scripts.BIRD.extract import (
    BIRD_COLUMN_DOMAIN_OPTIONS,
    STATIC_PIPELINE,
    build_parser,
)


def test_bird_column_domain_policy_stays_exact_and_pairwise():
    options = _resolve_options(None, **BIRD_COLUMN_DOMAIN_OPTIONS)

    assert STATIC_PIPELINE == ["db_column_stats", "db_fk_validate", "db_column_domain"]
    assert options.value_match_method == "sql"
    assert not options.domain_filter_enabled
    assert not options.column_domain_enabled
    assert not options.pattern_table_domain_enabled
    assert [stage.name for stage in options.filter_pipeline] == ["value_overlap"]


def test_bird_extract_cli_keeps_pipeline_modes_unambiguous():
    parser = build_parser()
    default = parser.parse_args([])
    static = parser.parse_args(["california_schools", "--static-only"])
    agent = parser.parse_args(["california_schools", "--agent-only"])

    assert default.db is None
    assert not default.static_only and not default.ai_only and not default.agent_only
    assert static.static_only and not static.ai_only and not static.agent_only
    assert agent.agent_only and not agent.static_only and not agent.ai_only
