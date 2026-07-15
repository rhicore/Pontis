"""Hybrid exact/approximate column top-k regressions."""

from extractor.db_column_stats import _profile_cursor


class _Cursor:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter((value,) for value in self.values)


def _profile(values, *, cardinality_mode="exact"):
    return _profile_cursor(
        _Cursor(values),
        "INT",
        sample_size=3,
        topk_size=5,
        cardinality_mode=cardinality_mode,
    )


def test_small_domain_topk_is_exact():
    stats = _profile([1, 1, 1, 2, 2, 3])

    assert stats["topk_method"] == "exact_counter"
    assert stats["topk"][:3] == [
        {"value": 1, "count": 3, "percentage": 50.0},
        {"value": 2, "count": 2, "percentage": 33.33},
        {"value": 3, "count": 1, "percentage": 16.67},
    ]


def test_high_cardinality_unique_values_do_not_publish_false_heavy_hitters():
    values = list(range(5000))
    stats = _profile(values)

    assert stats["topk_method"] == "space_saving_with_bounds"
    assert stats["topk"] == []


def test_high_cardinality_real_heavy_hitter_keeps_error_bounds():
    values = list(range(5000)) + [99999] * 200
    stats = _profile(values)

    top = stats["topk"][0]
    assert top["value"] == 99999
    assert top["approximate"] is True
    assert top["count_lower_bound"] <= 200 <= top["count"]
    assert top["count_error"] == top["count"] - top["count_lower_bound"]


def test_auto_cardinality_is_exact_for_small_domains():
    stats = _profile(range(100), cardinality_mode="auto")

    assert stats["cardinality"] == 100
    assert stats["cardinality_method"] == "count_distinct"
    assert stats["cardinality_lower_bound"] == 100
    assert stats["cardinality_upper_bound"] == 100


def test_auto_cardinality_uses_bounded_sketch_for_large_domains():
    stats = _profile(range(20_000), cardinality_mode="auto")

    assert stats["cardinality_method"] == "kmv_sketch"
    assert stats["cardinality_lower_bound"] <= 20_000 <= stats["cardinality_upper_bound"]
    assert abs(stats["cardinality"] - 20_000) / 20_000 < 0.08
