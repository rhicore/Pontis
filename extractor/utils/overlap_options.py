"""Overlap extractor configuration and shared types."""
from __future__ import annotations

import logging
import json
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

BOOLEAN_VALUES = {"0", "1", "true", "false", "t", "f", "yes", "no", "y", "n"}
NUMERIC_TYPES = {"int", "integer", "real", "float", "double", "decimal", "numeric"}
TEMPORAL_TYPES = {"date", "datetime", "timestamp", "time"}
MIN_OVERLAP_VALUES = 10
MIN_OVERLAP_COVERAGE_OVERRIDE = 0.8
SHORT_CODE_MAX_LENGTH = 4
SHORT_CODE_RATIO_THRESHOLD = 0.8
SHORT_CODE_MAX_COVERAGE = 0.5
MIN_GROUP_SIZE = 3
MAX_NAME_GROUP_COLUMNS = 12
DEFAULT_MAX_VALUE_CANDIDATE_PAIRS = 5000
INTERSECTION_SAMPLE_LIMIT = 25
TABLE_COLUMN_BATCH_SIZE = 32
DEFAULT_GENERIC_TOKEN_TOP_K = 5
DEFAULT_MINHASH_NUM_PERM = 128
DEFAULT_MINHASH_MIN_MATCHING_HASHES = 1
DEFAULT_MINHASH_MAX_SQL_VERIFY_PAIRS = 5000
DEFAULT_SNOWFLAKE_MINHASH_COLUMN_BATCH_SIZE = 128
DEFAULT_SNOWFLAKE_MINHASH_VALUE_PARTITIONS = 1
DEFAULT_SNOWFLAKE_MINHASH_MAX_WAREHOUSE_RUNNING = 0
DEFAULT_SNOWFLAKE_MINHASH_WAREHOUSE_POLL_SECONDS = 30
DEFAULT_LAZO_CONTAINMENT_THRESHOLD = 0.01
DEFAULT_LAZO_CONFIDENCE = 0.99
MINHASH_MODULUS = (1 << 61) - 1
VALUE_MATCH_METHODS = {
    "sql",
    "minhash",
    "minhash_then_sql",
    "snowflake_minhash",
    "snowflake_lazo",
    "hash_index",
    "sample_bloom",
    "sample_bloom_then_sql",
    "adaptive_sample_bloom",
    "snowflake_adaptive_probe",
    "metadata_sample",
}
HASH_INDEX_FETCH_SIZE = 50000
DEFAULT_SAMPLE_BLOOM_SAMPLE_SIZE = 2048
DEFAULT_SAMPLE_BLOOM_FALSE_POSITIVE_RATE = 0.0001
DEFAULT_SAMPLE_BLOOM_INITIAL_CAPACITY = 8192
DEFAULT_SAMPLE_BLOOM_GROWTH_FACTOR = 4
DEFAULT_SAMPLE_BLOOM_MIN_HITS = 1
DEFAULT_SAMPLE_BLOOM_SAMPLE_ROWS = 0
DEFAULT_SAMPLE_BLOOM_MAX_DOMAIN_MEMBERS = 0
DEFAULT_ADAPTIVE_SAMPLE_INITIAL_SIZE = 256
DEFAULT_ADAPTIVE_SAMPLE_SIZE = 1024
DEFAULT_ADAPTIVE_SAMPLE_MAX_SIZE = 4096
DEFAULT_ADAPTIVE_SAMPLE_MIN_OVERLAP = 0.01
DEFAULT_ADAPTIVE_SAMPLE_CONFIDENCE = 0.99
DEFAULT_ADAPTIVE_PROBE_PARALLEL_QUERIES = 8
DEFAULT_ADAPTIVE_PROBE_TABLES_PER_QUERY = 1
DEFAULT_ADAPTIVE_PROBE_TARGET_COLUMNS_PER_QUERY = 4
DEFAULT_ADAPTIVE_PROBE_PROFILE_COLUMNS_PER_QUERY = 16
DEFAULT_ADAPTIVE_PROBE_PROFILE_SAMPLE_ROWS = 4096

STOP_TOKENS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or",
    "this", "that", "field", "column", "table",
}

GENERIC_KEY_TOKENS = {
    "id", "ids", "key", "code", "name", "value", "uuid", "guid",
    "uid", "number", "num", "type",
}
KEYLIKE_TOKENS = {
    "id", "ids", "key", "code", "uuid", "guid", "identifier",
    "patent", "application", "publication", "family", "citation",
    "user", "visitor", "customer", "person", "patient", "participant", "subject",
    "case", "sample", "specimen", "aliquot", "barcode",
    "geo", "country", "state", "county", "tract", "block", "zip", "postal",
    "repo", "repository", "file", "path", "commit",
    "order", "product", "item", "assignee", "inventor",
    "station", "site", "location", "symbol", "address",
}
KEYLIKE_SUFFIXES = ("_id", "id", "_key", "_code", "_uuid", "_guid", "barcode")
COLUMN_ROLE_SLOT_RE = re.compile(r"(?:(?<=_)|^)\d{1,3}(?=_|$)")
DEFAULT_PATTERN_TABLE_DOMAIN_THRESHOLD = 0.8
MIN_PATTERN_TABLE_DOMAIN_ROLES = 5


@dataclass(frozen=True)
class OverlapFilterSpec:
    """One ordered, thresholded candidate filter."""

    name: str
    threshold: float = 1.0
    metric: str = "score"


@dataclass(frozen=True)
class OverlapOptions:
    """Runtime switches for the storage-backed overlap extractor."""

    value_overlap_enabled: bool = True
    name_overlap_enabled: bool = True
    same_schema_only: bool = False
    skip_same_table_group: bool = True
    same_table_overlap_enabled: bool = True
    same_table_group_representative_only: bool = True
    domain_filter_enabled: bool = True
    shape_filter_enabled: bool = False
    key_like_only: bool = False
    require_name_token_overlap: bool = False
    name_token_overlap_first: bool = False
    require_repeated_key_name: bool = False
    top_k_per_column: int = 0
    generic_token_top_k: int = DEFAULT_GENERIC_TOKEN_TOP_K
    max_value_candidate_pairs: int = DEFAULT_MAX_VALUE_CANDIDATE_PAIRS
    value_match_method: str = "sql"
    minhash_num_perm: int = DEFAULT_MINHASH_NUM_PERM
    minhash_min_matching_hashes: int = DEFAULT_MINHASH_MIN_MATCHING_HASHES
    minhash_jaccard_threshold: float = 0.0
    minhash_max_sql_verify_pairs: int = DEFAULT_MINHASH_MAX_SQL_VERIFY_PAIRS
    snowflake_minhash_column_batch_size: int = DEFAULT_SNOWFLAKE_MINHASH_COLUMN_BATCH_SIZE
    snowflake_minhash_value_partitions: int = DEFAULT_SNOWFLAKE_MINHASH_VALUE_PARTITIONS
    snowflake_minhash_max_warehouse_running: int = DEFAULT_SNOWFLAKE_MINHASH_MAX_WAREHOUSE_RUNNING
    snowflake_minhash_warehouse_poll_seconds: int = DEFAULT_SNOWFLAKE_MINHASH_WAREHOUSE_POLL_SECONDS
    lazo_containment_threshold: float = DEFAULT_LAZO_CONTAINMENT_THRESHOLD
    lazo_confidence: float = DEFAULT_LAZO_CONFIDENCE
    sample_bloom_sample_size: int = DEFAULT_SAMPLE_BLOOM_SAMPLE_SIZE
    sample_bloom_false_positive_rate: float = DEFAULT_SAMPLE_BLOOM_FALSE_POSITIVE_RATE
    sample_bloom_initial_capacity: int = DEFAULT_SAMPLE_BLOOM_INITIAL_CAPACITY
    sample_bloom_growth_factor: int = DEFAULT_SAMPLE_BLOOM_GROWTH_FACTOR
    sample_bloom_min_hits: int = DEFAULT_SAMPLE_BLOOM_MIN_HITS
    sample_bloom_sample_rows: int = DEFAULT_SAMPLE_BLOOM_SAMPLE_ROWS
    sample_bloom_max_domain_members: int = DEFAULT_SAMPLE_BLOOM_MAX_DOMAIN_MEMBERS
    adaptive_sample_initial_size: int = DEFAULT_ADAPTIVE_SAMPLE_INITIAL_SIZE
    adaptive_sample_size: int = DEFAULT_ADAPTIVE_SAMPLE_SIZE
    adaptive_sample_max_size: int = DEFAULT_ADAPTIVE_SAMPLE_MAX_SIZE
    adaptive_sample_min_overlap: float = DEFAULT_ADAPTIVE_SAMPLE_MIN_OVERLAP
    adaptive_sample_confidence: float = DEFAULT_ADAPTIVE_SAMPLE_CONFIDENCE
    adaptive_probe_parallel_queries: int = DEFAULT_ADAPTIVE_PROBE_PARALLEL_QUERIES
    adaptive_probe_tables_per_query: int = DEFAULT_ADAPTIVE_PROBE_TABLES_PER_QUERY
    adaptive_probe_target_columns_per_query: int = DEFAULT_ADAPTIVE_PROBE_TARGET_COLUMNS_PER_QUERY
    adaptive_probe_full_membership_enabled: bool = False
    adaptive_probe_name_fallback_enabled: bool = False
    adaptive_probe_name_fallback_top_k: int = 0
    adaptive_probe_profile_columns_per_query: int = DEFAULT_ADAPTIVE_PROBE_PROFILE_COLUMNS_PER_QUERY
    adaptive_probe_profile_sample_rows: int = DEFAULT_ADAPTIVE_PROBE_PROFILE_SAMPLE_ROWS
    group_policy_enabled: bool = False
    group_drop_name_only: bool = False
    group_drop_local_ordinal: bool = False
    group_drop_low_overlap_text: bool = False
    group_low_overlap_text_threshold: float = 0.1
    group_auto_accept_min_overlap: float = 0.1
    column_domain_enabled: bool = False
    pattern_table_domain_enabled: bool = False
    pattern_table_domain_threshold: float = DEFAULT_PATTERN_TABLE_DOMAIN_THRESHOLD
    filter_pipeline: tuple[OverlapFilterSpec, ...] = ()


@dataclass
class MinHashProfile:
    """Distinct-value MinHash profile for one physical column."""

    cardinality: int
    signature: tuple[int, ...]
    sample_values: list[str]


@dataclass
class SnowflakeMinHashProfile:
    """Compact server-side MinHash plus approximate distinct cardinality."""

    cardinality: int
    signature: tuple[int, ...]
    member_refs: tuple[str, ...] = ()
    cardinality_lower_bound: int | None = None
    cardinality_upper_bound: int | None = None


@dataclass
class BloomLayer:
    """One layer of a scalable Bloom filter."""

    bit_count: int
    hash_count: int
    capacity: int
    count: int
    bits: bytearray


@dataclass
class SampleBloomProfile:
    """Containment-oriented profile for estimating coverage_min."""

    cardinality: int
    sample_hashes: tuple[int, ...]
    layers: tuple[BloomLayer, ...]
    member_refs: tuple[str, ...] = ()


@dataclass
class AdaptiveProbeProfile:
    """Server-side bottom-k sample without a downloaded full membership index."""

    cardinality: int
    sample_hashes: tuple[int, ...]
    member_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateFilterContext:
    """Cheap facts reused by the pre-value candidate filter stages."""

    name_token_overlap: bool
    repeated_key_names: set[str]


def _resolve_options(config=None, **overrides) -> OverlapOptions:
    """Resolve module kwargs, config attributes, and environment switches."""

    def bool_value(name: str, default: bool) -> bool:
        if overrides.get(name) is not None:
            return bool(overrides[name])
        config_value = _config_attr(config, f"overlap_{name}", None)
        if config_value is not None:
            return _coerce_bool(config_value, default)
        env_value = os.environ.get(f"PONTIS_OVERLAP_{_env_name(name)}")
        if env_value is not None:
            return _coerce_bool(env_value, default)
        return default

    def int_value(name: str, default: int) -> int:
        if overrides.get(name) is not None:
            return max(0, int(overrides[name]))
        config_value = _config_attr(config, f"overlap_{name}", None)
        if config_value is not None:
            return _coerce_int(config_value, default)
        env_value = os.environ.get(f"PONTIS_OVERLAP_{_env_name(name)}")
        if env_value is not None:
            return _coerce_int(env_value, default)
        return default

    def float_value(name: str, default: float) -> float:
        if overrides.get(name) is not None:
            return max(0.0, float(overrides[name]))
        config_value = _config_attr(config, f"overlap_{name}", None)
        if config_value is not None:
            return _coerce_float(config_value, default)
        env_value = os.environ.get(f"PONTIS_OVERLAP_{_env_name(name)}")
        if env_value is not None:
            return _coerce_float(env_value, default)
        return default

    def str_value(name: str, default: str, valid: set[str] | None = None) -> str:
        value = overrides.get(name)
        if value is None:
            value = _config_attr(config, f"overlap_{name}", None)
        if value is None:
            value = os.environ.get(f"PONTIS_OVERLAP_{_env_name(name)}")
        if value is None:
            return default
        text = str(value).strip().lower()
        if valid and text not in valid:
            logger.warning("Invalid overlap option %s=%r, using %s", name, value, default)
            return default
        return text

    def raw_value(name: str):
        if overrides.get(name) is not None:
            return overrides[name]
        config_value = _config_attr(config, f"overlap_{name}", None)
        if config_value is not None:
            return config_value
        return os.environ.get(f"PONTIS_OVERLAP_{_env_name(name)}")

    max_pairs = int_value("max_value_candidate_pairs", DEFAULT_MAX_VALUE_CANDIDATE_PAIRS)
    legacy_max = os.environ.get("PONTIS_OVERLAP_MAX_VALUE_PAIRS")
    if overrides.get("max_value_candidate_pairs") is None and legacy_max:
        max_pairs = _coerce_int(legacy_max, DEFAULT_MAX_VALUE_CANDIDATE_PAIRS)

    return OverlapOptions(
        value_overlap_enabled=bool_value("value_overlap_enabled", True),
        name_overlap_enabled=bool_value("name_overlap_enabled", True),
        same_schema_only=bool_value("same_schema_only", False),
        skip_same_table_group=bool_value("skip_same_table_group", True),
        same_table_overlap_enabled=bool_value("same_table_overlap_enabled", True),
        same_table_group_representative_only=bool_value("same_table_group_representative_only", True),
        domain_filter_enabled=bool_value("domain_filter_enabled", True),
        shape_filter_enabled=bool_value("shape_filter_enabled", False),
        key_like_only=bool_value("key_like_only", False),
        require_name_token_overlap=bool_value("require_name_token_overlap", False),
        name_token_overlap_first=bool_value("name_token_overlap_first", False),
        require_repeated_key_name=bool_value("require_repeated_key_name", False),
        top_k_per_column=int_value("top_k_per_column", 0),
        generic_token_top_k=int_value("generic_token_top_k", DEFAULT_GENERIC_TOKEN_TOP_K),
        max_value_candidate_pairs=max_pairs,
        value_match_method=str_value("value_match_method", "sql", VALUE_MATCH_METHODS),
        minhash_num_perm=max(1, int_value("minhash_num_perm", DEFAULT_MINHASH_NUM_PERM)),
        minhash_min_matching_hashes=max(1, int_value("minhash_min_matching_hashes", DEFAULT_MINHASH_MIN_MATCHING_HASHES)),
        minhash_jaccard_threshold=float_value("minhash_jaccard_threshold", 0.0),
        minhash_max_sql_verify_pairs=int_value("minhash_max_sql_verify_pairs", DEFAULT_MINHASH_MAX_SQL_VERIFY_PAIRS),
        snowflake_minhash_column_batch_size=max(
            1,
            int_value("snowflake_minhash_column_batch_size", DEFAULT_SNOWFLAKE_MINHASH_COLUMN_BATCH_SIZE),
        ),
        snowflake_minhash_value_partitions=max(
            1,
            int_value("snowflake_minhash_value_partitions", DEFAULT_SNOWFLAKE_MINHASH_VALUE_PARTITIONS),
        ),
        snowflake_minhash_max_warehouse_running=max(
            0,
            int_value("snowflake_minhash_max_warehouse_running", DEFAULT_SNOWFLAKE_MINHASH_MAX_WAREHOUSE_RUNNING),
        ),
        snowflake_minhash_warehouse_poll_seconds=max(
            1,
            int_value("snowflake_minhash_warehouse_poll_seconds", DEFAULT_SNOWFLAKE_MINHASH_WAREHOUSE_POLL_SECONDS),
        ),
        lazo_containment_threshold=min(
            1.0,
            float_value("lazo_containment_threshold", DEFAULT_LAZO_CONTAINMENT_THRESHOLD),
        ),
        lazo_confidence=min(
            0.999999,
            max(0.5, float_value("lazo_confidence", DEFAULT_LAZO_CONFIDENCE)),
        ),
        sample_bloom_sample_size=max(1, int_value("sample_bloom_sample_size", DEFAULT_SAMPLE_BLOOM_SAMPLE_SIZE)),
        sample_bloom_false_positive_rate=min(
            0.5,
            max(0.000001, float_value("sample_bloom_false_positive_rate", DEFAULT_SAMPLE_BLOOM_FALSE_POSITIVE_RATE)),
        ),
        sample_bloom_initial_capacity=max(1, int_value("sample_bloom_initial_capacity", DEFAULT_SAMPLE_BLOOM_INITIAL_CAPACITY)),
        sample_bloom_growth_factor=max(2, int_value("sample_bloom_growth_factor", DEFAULT_SAMPLE_BLOOM_GROWTH_FACTOR)),
        sample_bloom_min_hits=max(1, int_value("sample_bloom_min_hits", DEFAULT_SAMPLE_BLOOM_MIN_HITS)),
        sample_bloom_sample_rows=int_value("sample_bloom_sample_rows", DEFAULT_SAMPLE_BLOOM_SAMPLE_ROWS),
        sample_bloom_max_domain_members=int_value(
            "sample_bloom_max_domain_members",
            DEFAULT_SAMPLE_BLOOM_MAX_DOMAIN_MEMBERS,
        ),
        adaptive_sample_initial_size=max(
            1,
            int_value("adaptive_sample_initial_size", DEFAULT_ADAPTIVE_SAMPLE_INITIAL_SIZE),
        ),
        adaptive_sample_size=max(1, int_value("adaptive_sample_size", DEFAULT_ADAPTIVE_SAMPLE_SIZE)),
        adaptive_sample_max_size=max(
            1,
            int_value("adaptive_sample_max_size", DEFAULT_ADAPTIVE_SAMPLE_MAX_SIZE),
        ),
        adaptive_sample_min_overlap=min(
            1.0,
            float_value("adaptive_sample_min_overlap", DEFAULT_ADAPTIVE_SAMPLE_MIN_OVERLAP),
        ),
        adaptive_sample_confidence=min(
            0.999999,
            max(0.5, float_value("adaptive_sample_confidence", DEFAULT_ADAPTIVE_SAMPLE_CONFIDENCE)),
        ),
        adaptive_probe_parallel_queries=max(
            1,
            int_value("adaptive_probe_parallel_queries", DEFAULT_ADAPTIVE_PROBE_PARALLEL_QUERIES),
        ),
        adaptive_probe_tables_per_query=max(
            1,
            int_value("adaptive_probe_tables_per_query", DEFAULT_ADAPTIVE_PROBE_TABLES_PER_QUERY),
        ),
        adaptive_probe_target_columns_per_query=max(
            1,
            int_value(
                "adaptive_probe_target_columns_per_query",
                DEFAULT_ADAPTIVE_PROBE_TARGET_COLUMNS_PER_QUERY,
            ),
        ),
        adaptive_probe_full_membership_enabled=bool_value(
            "adaptive_probe_full_membership_enabled",
            False,
        ),
        adaptive_probe_name_fallback_enabled=bool_value(
            "adaptive_probe_name_fallback_enabled",
            False,
        ),
        adaptive_probe_name_fallback_top_k=int_value(
            "adaptive_probe_name_fallback_top_k",
            0,
        ),
        adaptive_probe_profile_columns_per_query=max(
            1,
            int_value(
                "adaptive_probe_profile_columns_per_query",
                DEFAULT_ADAPTIVE_PROBE_PROFILE_COLUMNS_PER_QUERY,
            ),
        ),
        adaptive_probe_profile_sample_rows=max(
            1,
            int_value(
                "adaptive_probe_profile_sample_rows",
                DEFAULT_ADAPTIVE_PROBE_PROFILE_SAMPLE_ROWS,
            ),
        ),
        group_policy_enabled=bool_value("group_policy_enabled", False),
        group_drop_name_only=bool_value("group_drop_name_only", False),
        group_drop_local_ordinal=bool_value("group_drop_local_ordinal", False),
        group_drop_low_overlap_text=bool_value("group_drop_low_overlap_text", False),
        group_low_overlap_text_threshold=min(1.0, float_value("group_low_overlap_text_threshold", 0.1)),
        group_auto_accept_min_overlap=min(1.0, float_value("group_auto_accept_min_overlap", 0.1)),
        column_domain_enabled=bool_value("column_domain_enabled", False),
        pattern_table_domain_enabled=bool_value("pattern_table_domain_enabled", False),
        pattern_table_domain_threshold=float_value("pattern_table_domain_threshold", DEFAULT_PATTERN_TABLE_DOMAIN_THRESHOLD),
        filter_pipeline=_parse_filter_pipeline(raw_value("filter_pipeline")),
    )


def _parse_filter_pipeline(value: Any) -> tuple[OverlapFilterSpec, ...]:
    """Parse an ordered pipeline from config/module kwargs/environment.

    Module kwargs should use a list of mappings. JSON is accepted for config
    files and environment variables; a comma-separated name list is useful for
    quick local runs and assigns every stage its default threshold of one.
    """

    if value in (None, "", []):
        return ()
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        if text.startswith("[") or text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("Invalid overlap_filter_pipeline JSON; ignoring it")
                return ()
        else:
            raw = [{"name": name.strip()} for name in text.split(",") if name.strip()]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        logger.warning("Invalid overlap_filter_pipeline=%r; ignoring it", value)
        return ()

    specs: list[OverlapFilterSpec] = []
    for item in raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            logger.warning("Ignoring invalid overlap filter specification: %r", item)
            continue
        name = str(item.get("name") or "").strip().lower()
        if not name:
            logger.warning("Ignoring overlap filter without a name: %r", item)
            continue
        try:
            threshold = float(item.get("threshold", 1.0))
        except (TypeError, ValueError):
            logger.warning("Invalid threshold for overlap filter %s: %r", name, item.get("threshold"))
            continue
        metric = str(item.get("metric") or "score").strip().lower()
        specs.append(OverlapFilterSpec(name=name, threshold=max(0.0, threshold), metric=metric))
    return tuple(specs)


def _config_attr(config, name: str, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def _env_name(name: str) -> str:
    return str(name).upper()


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    logger.warning("Invalid boolean overlap option %r, using %s", value, default)
    return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        logger.warning("Invalid integer overlap option %r, using %s", value, default)
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        logger.warning("Invalid float overlap option %r, using %s", value, default)
        return default
