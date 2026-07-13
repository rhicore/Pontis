from extractor.utils.online_value_domains import (
    OnlineValueDomainConfig,
    ValueColumn,
    build_online_value_domains,
)


def _column(ref: str, values: set[int], bucket: str = "text") -> ValueColumn:
    return ValueColumn(ref=ref, values=frozenset(values), bucket=bucket)


def test_repeated_domains_reduce_comparisons():
    columns = [
        _column(f"domain-{domain}-column-{member}", {domain * 1000 + value for value in range(20)})
        for member in range(10)
        for domain in range(10)
    ]

    result = build_online_value_domains(
        columns,
        OnlineValueDomainConfig(overlap_threshold=0.8, match_policy="union"),
    )

    assert len(result.domains) == 10
    assert result.domain_comparisons < len(columns) * (len(columns) - 1) // 2
    assert all(len(domain.members) == 10 for domain in result.domains)


def test_union_only_exposes_transitive_chain_pollution():
    columns = [
        _column("a", {1, 2}),
        _column("b", {2, 3}),
        _column("c", {3, 4}),
    ]

    result = build_online_value_domains(
        columns,
        OnlineValueDomainConfig(overlap_threshold=0.5, match_policy="union"),
    )

    assert len(result.domains) == 1
    assert result.assignments["a"] == result.assignments["c"]


def test_anchor_support_can_block_transitive_chain_pollution():
    columns = [
        _column("a", {1, 2}),
        _column("b", {2, 3}),
        _column("c", {3, 4}),
    ]

    result = build_online_value_domains(
        columns,
        OnlineValueDomainConfig(
            overlap_threshold=0.5,
            match_policy="union_and_anchor",
            min_anchor_support=1.0,
        ),
    )

    assert len(result.domains) == 2
    assert result.assignments["a"] == result.assignments["b"]
    assert result.assignments["a"] != result.assignments["c"]


def test_bucket_prevents_incompatible_domain_comparisons():
    result = build_online_value_domains([
        _column("numeric-id", {1, 2}, "numeric"),
        _column("text-code", {1, 2}, "text"),
    ])

    assert len(result.domains) == 2
    assert result.domain_comparisons == 0
