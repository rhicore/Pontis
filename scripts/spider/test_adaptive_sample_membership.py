from dataclasses import replace

from extractor.utils.overlap_options import AdaptiveProbeProfile, OverlapOptions, SampleBloomProfile
from extractor.utils.overlap_value_matchers import (
    _adaptive_probe_kmv_overlap_bounds,
    _estimate_overlap_from_adaptive_sample_bloom,
    _new_sample_bloom_profile,
    _sample_bloom_add,
    _wilson_interval,
)


def _options() -> OverlapOptions:
    return replace(
        OverlapOptions(),
        value_match_method="adaptive_sample_bloom",
        sample_bloom_false_positive_rate=0.000001,
        sample_bloom_initial_capacity=1024,
        adaptive_sample_initial_size=4,
        adaptive_sample_size=8,
        adaptive_sample_max_size=16,
        adaptive_sample_min_overlap=0.25,
        adaptive_sample_confidence=0.95,
    )


def _profiles(contained_hashes: set[int]) -> tuple[SampleBloomProfile, SampleBloomProfile, dict]:
    options = _options()
    small = SampleBloomProfile(cardinality=16, sample_hashes=tuple(range(16)), layers=())
    large = _new_sample_bloom_profile(options)
    for value_hash in contained_hashes:
        _sample_bloom_add(large, value_hash, options)
    large.cardinality = 100
    return small, large, {"small": small, "large": large}


def test_adaptive_sample_retains_high_containment():
    options = _options()
    small, large, profiles = _profiles(set(range(0, 16, 2)))
    result = _estimate_overlap_from_adaptive_sample_bloom(
        {"entity_name": "small"}, {"entity_name": "large"}, small, large, profiles, options
    )

    assert result is not None
    assert result["overlap_coefficient"] > 0.45
    assert result["method"] == "adaptive_sample_bloom"
    assert result["sample_size"] <= 16
    assert result["stages_evaluated"]


def test_adaptive_sample_rejects_zero_containment():
    options = _options()
    small, large, profiles = _profiles(set(range(100, 116)))
    result = _estimate_overlap_from_adaptive_sample_bloom(
        {"entity_name": "small"}, {"entity_name": "large"}, small, large, profiles, options
    )

    assert result is None


def test_wilson_interval_contracts_with_more_samples():
    low_small, high_small = _wilson_interval(0, 256, 0.99)
    low_large, high_large = _wilson_interval(0, 1024, 0.99)

    assert low_small == low_large == 0.0
    assert high_large < 0.01 < high_small


def test_kmv_upper_bound_rejects_equal_size_disjoint_domains():
    options = replace(
        _options(),
        adaptive_sample_max_size=4096,
        adaptive_sample_min_overlap=0.01,
        adaptive_sample_confidence=0.99,
    )
    left = AdaptiveProbeProfile(cardinality=100_000, sample_hashes=tuple(range(4096)))
    right = AdaptiveProbeProfile(cardinality=100_000, sample_hashes=tuple(range(4096, 8192)))

    estimate, upper = _adaptive_probe_kmv_overlap_bounds(left, right, options)

    assert estimate == 0.0
    assert upper < options.adaptive_sample_min_overlap


def test_kmv_upper_bound_keeps_skewed_domains_for_membership_probe():
    options = replace(
        _options(),
        adaptive_sample_max_size=4096,
        adaptive_sample_min_overlap=0.01,
        adaptive_sample_confidence=0.99,
    )
    left = AdaptiveProbeProfile(cardinality=100_000, sample_hashes=tuple(range(4096)))
    right = AdaptiveProbeProfile(cardinality=10_000_000, sample_hashes=tuple(range(4096, 8192)))

    estimate, upper = _adaptive_probe_kmv_overlap_bounds(left, right, options)

    assert estimate == 0.0
    assert upper >= options.adaptive_sample_min_overlap


def test_kmv_estimates_high_overlap():
    options = replace(_options(), adaptive_sample_max_size=4096)
    left = AdaptiveProbeProfile(cardinality=100_000, sample_hashes=tuple(range(4096)))
    right = AdaptiveProbeProfile(cardinality=100_000, sample_hashes=tuple(range(2048, 6144)))

    estimate, upper = _adaptive_probe_kmv_overlap_bounds(left, right, options)

    assert estimate > 0.6
    assert upper >= estimate
