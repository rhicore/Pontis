"""Value-evidence matchers for column overlap candidates."""
from __future__ import annotations

import base64
import hashlib
import heapq
import json
import logging
import math
import os
import re
import time
from array import array
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Optional

from extractor.utils.overlap_candidates import _name_token_overlap_candidate
from extractor.utils.overlap_options import (
    BOOLEAN_VALUES,
    BloomLayer,
    HASH_INDEX_FETCH_SIZE,
    INTERSECTION_SAMPLE_LIMIT,
    MINHASH_MODULUS,
    MIN_OVERLAP_COVERAGE_OVERRIDE,
    MIN_OVERLAP_VALUES,
    MinHashProfile,
    NUMERIC_TYPES,
    OverlapOptions,
    SampleBloomProfile,
    SnowflakeMinHashProfile,
    SHORT_CODE_MAX_COVERAGE,
    SHORT_CODE_MAX_LENGTH,
    SHORT_CODE_RATIO_THRESHOLD,
    TEMPORAL_TYPES,
)

logger = logging.getLogger(__name__)

def _qualified_table_sql(col: Dict, dialect: str) -> str:
    table = _quote_identifier(col["table_name"], dialect)
    schema_name = str(col.get("schema_name") or "").strip()
    db_name = str(col.get("db_ref") or "").strip()
    if dialect == "snowflake" and db_name and schema_name:
        return f"{_quote_identifier(db_name, dialect)}.{_quote_identifier(schema_name, dialect)}.{table}"
    if schema_name and dialect not in {"sqlite", "duckdb"}:
        return f"{_quote_identifier(schema_name, dialect)}.{table}"
    return table


def _quote_identifier(name: str, dialect: str = "") -> str:
    text = str(name or "")
    return '"' + text.replace('"', '""') + '"'


def _detect_column_overlaps_sql(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    """Detect value-domain overlaps with SQL-side DISTINCT/intersection counts."""

    overlaps: list[Dict] = []
    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            overlaps = _detect_column_overlaps_sql_cursor(cursor, dialect, candidates, options)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Could not open database for SQL overlap detection: %s", exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    overlaps.sort(key=lambda item: (-item["stats"]["overlap_coefficient"], item["from_ref"], item["to_ref"]))
    return overlaps


def _detect_column_overlaps_sql_cursor(
    cursor,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    overlaps: list[Dict] = []
    for col1, col2 in candidates:
        overlap_result = _calculate_overlap_sql(cursor, col1, col2, dialect, options)
        if not overlap_result:
            continue
        overlaps.append(_pair_overlap_payload(col1, col2, overlap_result))
    overlaps.sort(key=lambda item: (-item["stats"]["overlap_coefficient"], item["from_ref"], item["to_ref"]))
    return overlaps


def _detect_column_overlaps(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    """Detect value-domain overlaps with the configured value matching method."""

    domain_compatible_methods = {
        "hash_index",
        "sample_bloom",
        "sample_bloom_then_sql",
        "metadata_sample",
        "snowflake_minhash",
        "snowflake_lazo",
    }
    if _has_column_domain_candidates(candidates) and options.value_match_method not in domain_compatible_methods:
        logger.warning("Column-domain candidates require hash_index/sample_bloom value matching; falling back to hash_index")
        return _detect_column_overlaps_hash_index(db_connect, dialect, candidates)
    if options.value_match_method == "sql":
        return _detect_column_overlaps_sql(db_connect, dialect, candidates, options)
    if options.value_match_method == "hash_index":
        return _detect_column_overlaps_hash_index(db_connect, dialect, candidates)
    if options.value_match_method == "snowflake_minhash":
        return _detect_column_overlaps_snowflake_minhash(db_connect, dialect, candidates, options)
    if options.value_match_method == "snowflake_lazo":
        return _detect_column_overlaps_snowflake_lazo(db_connect, dialect, candidates, options)
    if options.value_match_method == "metadata_sample":
        return _detect_column_overlaps_metadata_sample(candidates)
    if options.value_match_method in {"sample_bloom", "sample_bloom_then_sql"}:
        return _detect_column_overlaps_sample_bloom(db_connect, dialect, candidates, options)
    return _detect_column_overlaps_minhash(db_connect, dialect, candidates, options)


def _has_column_domain_candidates(candidates: list[tuple[Dict, Dict]]) -> bool:
    return any(_domain_members(col1) or _domain_members(col2) for col1, col2 in candidates)


def _detect_column_overlaps_metadata_sample(
    candidates: list[tuple[Dict, Dict]],
) -> List[Dict]:
    """Fast local value evidence from graph-provided sample/topk metadata."""

    overlaps: list[Dict] = []
    for col1, col2 in candidates:
        left_values = _metadata_sample_values(col1)
        right_values = _metadata_sample_values(col2)
        if not left_values or not right_values:
            continue
        hits = left_values & right_values
        if not hits:
            continue
        # Metadata samples are evidence only.  Do not extrapolate from a
        # member-cardinality sum: a logical column's distinct cardinality is
        # the union of its members and can be much smaller than that sum.
        left_card = len(left_values)
        right_card = len(right_values)
        sample_size = min(left_card, right_card)
        overlap_coefficient = len(hits) / sample_size if sample_size else 0.0
        overlaps.append(_pair_overlap_payload(col1, col2, {
            "overlap_coefficient": round(overlap_coefficient, 6),
            "sample_hits": len(hits),
            "sample_size": sample_size,
            "estimated": True,
            "method": "metadata_sample",
        }))
    overlaps.sort(key=lambda item: (
        -item["stats"].get("sample_hits", 0),
        -item["stats"]["overlap_coefficient"],
        item["from_ref"],
        item["to_ref"],
    ))
    logger.info("  Metadata-sample retained %s/%s value candidates", len(overlaps), len(candidates))
    return overlaps


def _metadata_sample_values(col: Dict) -> set[str]:
    values: set[str] = set()
    for member in _domain_members(col) or [col]:
        for value in member.get("sample") or []:
            normalized = _normalize_value(value)
            if normalized:
                values.add(normalized)
        for item in member.get("topk") or []:
            value = item.get("value") if isinstance(item, dict) else item
            normalized = _normalize_value(value)
            if normalized:
                values.add(normalized)
    return values


def _detect_column_overlaps_snowflake_minhash(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    """Estimate Jaccard with complete-column Snowflake MinHash sketches."""

    if dialect != "snowflake":
        logger.warning("snowflake_minhash requires Snowflake; falling back to local minhash")
        return _detect_column_overlaps_minhash(db_connect, dialect, candidates, options)
    if not candidates:
        return []

    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            available, reason = _value_database_available(cursor, dialect, candidates)
            if not available:
                logger.warning("Skipping Snowflake MinHash value check: %s", reason)
                return []
            profiles = _build_snowflake_minhash_profiles(cursor, candidates, options)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        raise RuntimeError("Could not build complete Snowflake MinHash profiles") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    overlaps: list[Dict] = []
    for left, right in candidates:
        left_profile = profiles.get(left["entity_name"])
        right_profile = profiles.get(right["entity_name"])
        if left_profile is None or right_profile is None:
            continue
        estimate = _snowflake_minhash_overlap_estimate(left_profile, right_profile, options)
        if estimate is None:
            continue
        overlaps.append(_pair_overlap_payload(left, right, estimate))
    overlaps.sort(key=lambda item: (-item["stats"]["overlap_coefficient"], item["from_ref"], item["to_ref"]))
    logger.info("  Snowflake MinHash retained %s/%s value candidates", len(overlaps), len(candidates))
    return overlaps


def _detect_column_overlaps_snowflake_lazo(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    """Estimate overlap/min with cardinality-aware MinHash confidence bounds.

    This is the LAZO-style value matcher for an already blocked candidate set.
    Cardinalities convert Jaccard estimates to containment/overlap coefficients.
    A pair is rejected only when the one-sided MinHash confidence upper bound
    is below the configured containment threshold. Thus zero-collision pairs
    with strongly skewed cardinalities remain as uncertain candidates instead
    of being incorrectly removed by a raw Jaccard collision gate.
    """

    if dialect != "snowflake":
        logger.warning("snowflake_lazo requires Snowflake; no value candidates produced")
        return []
    if not candidates:
        return []

    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            available, reason = _value_database_available(cursor, dialect, candidates)
            if not available:
                logger.warning("Skipping Snowflake LAZO value check: %s", reason)
                return []
            profiles = _build_snowflake_minhash_profiles(cursor, candidates, options)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Could not build Snowflake LAZO profiles: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    overlaps: list[Dict] = []
    uncertain_count = 0
    for left, right in candidates:
        left_profile = profiles.get(left["entity_name"])
        right_profile = profiles.get(right["entity_name"])
        if left_profile is None or right_profile is None:
            estimate = {
                "overlap_coefficient": 0.0,
                "overlap_coefficient_upper_bound": 1.0,
                "filter_score": 1.0,
                "containment_threshold": options.lazo_containment_threshold,
                "confidence": options.lazo_confidence,
                "decision": "profile_unavailable_retained",
                "estimated": True,
                "method": "snowflake_lazo",
            }
        else:
            estimate = _snowflake_lazo_overlap_estimate(left_profile, right_profile, options)
        if estimate is None:
            continue
        if estimate["decision"] == "uncertain_retained":
            uncertain_count += 1
        overlaps.append(_pair_overlap_payload(left, right, estimate))
    overlaps.sort(key=lambda item: (
        -item["stats"].get("overlap_coefficient", 0.0),
        -item["stats"].get("overlap_coefficient_upper_bound", 0.0),
        item["from_ref"],
        item["to_ref"],
    ))
    logger.info(
        "  Snowflake LAZO retained %s/%s value candidates (%s uncertain, threshold=%s, confidence=%s)",
        len(overlaps),
        len(candidates),
        uncertain_count,
        options.lazo_containment_threshold,
        options.lazo_confidence,
    )
    return overlaps


def _build_snowflake_minhash_profiles(
    cursor,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> dict[str, SnowflakeMinHashProfile]:
    physical_columns, domain_columns = _sample_bloom_profile_columns(candidates, options=options)
    profiles: dict[str, SnowflakeMinHashProfile] = {}
    missing_by_table: dict[str, list[Dict]] = defaultdict(list)
    for col in physical_columns.values():
        cached = _read_snowflake_minhash_profile(col, options)
        if cached is not None:
            profiles[col["entity_name"]] = cached
        else:
            missing_by_table[_column_table_key(col)].append(col)

    started = time.time()
    for table_key, columns in sorted(missing_by_table.items()):
        available, reason = _hash_index_table_available(cursor, columns[0], "snowflake")
        if not available:
            logger.warning("Skipping Snowflake MinHash profile for inaccessible table %s: %s", table_key, reason)
            continue
        for batch_start in range(0, len(columns), options.snowflake_minhash_column_batch_size):
            batch = columns[batch_start:batch_start + options.snowflake_minhash_column_batch_size]
            try:
                profiles.update(_query_snowflake_minhash_batch(cursor, batch, options))
            except Exception as exc:
                raise RuntimeError(
                    f"Snowflake MinHash profile generation failed for {table_key} "
                    f"({len(batch)} columns); refusing to emit incomplete overlap candidates"
                ) from exc
        logger.info(
            "  Built/loaded Snowflake MinHash profiles for table %s (%s/%s physical columns, %.1fs)",
            table_key,
            len(profiles),
            len(physical_columns),
            time.time() - started,
        )

    for ref, domain in domain_columns.items():
        profile = _combine_snowflake_minhash_domain(domain, profiles)
        if profile is not None:
            profiles[ref] = profile
    return profiles


def _query_snowflake_minhash_batch(
    cursor,
    columns: list[Dict],
    options: OverlapOptions,
) -> dict[str, SnowflakeMinHashProfile]:
    if len(columns) > 1:
        profiles: dict[str, SnowflakeMinHashProfile] = {}
        for col in columns:
            profiles.update(_query_snowflake_minhash_batch(cursor, [col], options))
        return profiles

    partition_count = options.snowflake_minhash_value_partitions
    col = columns[0]
    column_sql = _quote_identifier(col["column"], "snowflake")
    normalized = f"LOWER(TRIM(TO_VARCHAR({column_sql})))"
    raw_states: list[str] = []
    for partition in range(partition_count):
        _wait_for_snowflake_minhash_capacity(cursor, options)
        predicates = [f"{column_sql} IS NOT NULL", f"{normalized} <> ''"]
        if partition_count > 1:
            predicates.append(
                f"MOD(BITAND(HASH({normalized}), 2147483647), {partition_count}) = {partition}"
            )
        cursor.execute(
            f"SELECT MINHASH({options.minhash_num_perm}, {normalized}) "
            f"FROM {_qualified_table_sql(col, 'snowflake')} "
            f"WHERE {' AND '.join(predicates)}"
        )
        row = cursor.fetchone()
        if row is None:
            continue
        raw_state = str(row[0])
        if _snowflake_minhash_state(raw_state):
            raw_states.append(raw_state)

    if not raw_states:
        return {}
    signature = _snowflake_minhash_state(
        raw_states[0] if len(raw_states) == 1 else _combine_snowflake_minhash_states(cursor, raw_states)
    )
    if not signature:
        return {}
    profile = SnowflakeMinHashProfile(
        cardinality=1,
        signature=signature,
        cardinality_lower_bound=1,
        cardinality_upper_bound=1,
    )
    _write_snowflake_minhash_profile(col, profile, options)
    return {col["entity_name"]: profile}


def _wait_for_snowflake_minhash_capacity(cursor, options: OverlapOptions) -> None:
    max_running = options.snowflake_minhash_max_warehouse_running
    if max_running <= 0:
        return
    cursor.execute("SELECT CURRENT_WAREHOUSE()")
    row = cursor.fetchone()
    warehouse = str(row[0] if row else "").replace("'", "''")
    while warehouse:
        cursor.execute(f"SHOW WAREHOUSES LIKE '{warehouse}'")
        columns = [str(item[0]).lower() for item in cursor.description or ()]
        status = cursor.fetchone()
        if status is None:
            return
        running = int(status[columns.index("running")] or 0)
        queued = int(status[columns.index("queued")] or 0)
        if running <= max_running and queued == 0:
            return
        logger.info(
            "  Waiting for Snowflake warehouse capacity: running=%s, queued=%s, required running<=%s",
            running,
            queued,
            max_running,
        )
        time.sleep(options.snowflake_minhash_warehouse_poll_seconds)


def _combine_snowflake_minhash_states(cursor, raw_states: list[str]) -> str:
    state_rows = " UNION ALL ".join("SELECT PARSE_JSON(%s) AS state" for _ in raw_states)
    cursor.execute(f"SELECT MINHASH_COMBINE(state) FROM ({state_rows})", tuple(raw_states))
    row = cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _snowflake_minhash_state(value: Any) -> tuple[int, ...]:
    try:
        payload = json.loads(str(value))
        return tuple(int(item) for item in payload.get("state") or [])
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()


def _combine_snowflake_minhash_domain(
    domain: Dict,
    profiles: dict[str, SnowflakeMinHashProfile],
) -> SnowflakeMinHashProfile | None:
    domain_members = _domain_members(domain)
    members = [profiles.get(member["entity_name"]) for member in domain_members]
    if not members or any(profile is None for profile in members):
        return None
    signature_size = len(members[0].signature)
    if not signature_size or any(len(profile.signature) != signature_size for profile in members):
        return None
    cardinality_lower_bound = max(
        profile.cardinality_lower_bound or profile.cardinality
        for profile in members
    )
    cardinality_upper_bound = sum(
        profile.cardinality_upper_bound or profile.cardinality
        for profile in members
    )
    return SnowflakeMinHashProfile(
        cardinality=cardinality_upper_bound,
        signature=tuple(min(profile.signature[index] for profile in members) for index in range(signature_size)),
        member_refs=tuple(member["entity_name"] for member in _domain_members(domain)),
        cardinality_lower_bound=cardinality_lower_bound,
        cardinality_upper_bound=cardinality_upper_bound,
    )


def _snowflake_minhash_overlap_estimate(
    left: SnowflakeMinHashProfile,
    right: SnowflakeMinHashProfile,
    options: OverlapOptions,
) -> dict | None:
    signature_size = min(len(left.signature), len(right.signature))
    if not signature_size or not left.cardinality or not right.cardinality:
        return None
    matching_hashes = sum(
        left.signature[index] == right.signature[index]
        for index in range(signature_size)
    )
    if matching_hashes < options.minhash_min_matching_hashes:
        return None
    jaccard_estimate = matching_hashes / signature_size
    if jaccard_estimate < options.minhash_jaccard_threshold:
        return None
    return {
        "overlap_coefficient": round(jaccard_estimate, 6),
        "jaccard_estimate": round(jaccard_estimate, 6),
        "estimated": True,
        "method": "snowflake_minhash",
        "matching_hashes": matching_hashes,
        "signature_size": signature_size,
    }


def _snowflake_lazo_overlap_estimate(
    left: SnowflakeMinHashProfile,
    right: SnowflakeMinHashProfile,
    options: OverlapOptions,
) -> dict | None:
    signature_size = min(len(left.signature), len(right.signature))
    left_cardinality = int(left.cardinality or 0)
    right_cardinality = int(right.cardinality or 0)
    if not signature_size or left_cardinality <= 0 or right_cardinality <= 0:
        return None

    matching_hashes = sum(
        left.signature[index] == right.signature[index]
        for index in range(signature_size)
    )
    jaccard_estimate = matching_hashes / signature_size
    jaccard_upper = _binomial_proportion_upper_bound(
        matching_hashes,
        signature_size,
        options.lazo_confidence,
    )
    coefficient = _jaccard_to_overlap_coefficient(
        jaccard_estimate,
        left_cardinality,
        right_cardinality,
    )
    coefficient_upper = _jaccard_to_overlap_coefficient(
        jaccard_upper,
        *_max_ratio_cardinalities(left, right),
    )
    threshold = options.lazo_containment_threshold
    if coefficient_upper < threshold:
        return None

    required_jaccard = _overlap_coefficient_to_jaccard(
        threshold,
        *_max_ratio_cardinalities(left, right),
    )
    ratio = max(left_cardinality, right_cardinality) / min(left_cardinality, right_cardinality)
    return {
        "overlap_coefficient": round(coefficient, 9),
        "overlap_coefficient_upper_bound": round(coefficient_upper, 9),
        "jaccard_estimate": round(jaccard_estimate, 9),
        "jaccard_upper_bound": round(jaccard_upper, 9),
        "required_jaccard": round(required_jaccard, 12),
        "left_cardinality": left_cardinality,
        "right_cardinality": right_cardinality,
        "left_cardinality_lower_bound": left.cardinality_lower_bound or left_cardinality,
        "left_cardinality_upper_bound": left.cardinality_upper_bound or left_cardinality,
        "right_cardinality_lower_bound": right.cardinality_lower_bound or right_cardinality,
        "right_cardinality_upper_bound": right.cardinality_upper_bound or right_cardinality,
        "cardinality_ratio": round(ratio, 6),
        "cardinality_partition": int(math.floor(math.log2(ratio))) if ratio > 0 else 0,
        "containment_threshold": threshold,
        "confidence": options.lazo_confidence,
        "matching_hashes": matching_hashes,
        "signature_size": signature_size,
        "decision": "estimated_above_threshold" if coefficient >= threshold else "uncertain_retained",
        "filter_score": round(coefficient if coefficient >= threshold else coefficient_upper, 9),
        "estimated": True,
        "method": "snowflake_lazo",
    }


def _max_ratio_cardinalities(
    left: SnowflakeMinHashProfile,
    right: SnowflakeMinHashProfile,
) -> tuple[int, int]:
    """Return feasible cardinalities yielding the most conservative ratio."""

    left_lower = int(left.cardinality_lower_bound or left.cardinality)
    left_upper = int(left.cardinality_upper_bound or left.cardinality)
    right_lower = int(right.cardinality_lower_bound or right.cardinality)
    right_upper = int(right.cardinality_upper_bound or right.cardinality)
    if left_upper / max(1, right_lower) >= right_upper / max(1, left_lower):
        return max(1, left_upper), max(1, right_lower)
    return max(1, left_lower), max(1, right_upper)


def _jaccard_to_overlap_coefficient(
    jaccard: float,
    left_cardinality: int,
    right_cardinality: int,
) -> float:
    if jaccard <= 0.0:
        return 0.0
    intersection = jaccard * (left_cardinality + right_cardinality) / (1.0 + jaccard)
    return min(1.0, intersection / min(left_cardinality, right_cardinality))


def _overlap_coefficient_to_jaccard(
    coefficient: float,
    left_cardinality: int,
    right_cardinality: int,
) -> float:
    if coefficient <= 0.0:
        return 0.0
    intersection = coefficient * min(left_cardinality, right_cardinality)
    denominator = left_cardinality + right_cardinality - intersection
    return min(1.0, intersection / denominator) if denominator > 0 else 1.0


def _binomial_proportion_upper_bound(successes: int, trials: int, confidence: float) -> float:
    """One-sided upper bound for the MinHash collision probability.

    Zero collisions are the important LAZO/containment case. Its exact
    Clopper-Pearson bound prevents high-containment, highly skewed domains from
    being discarded merely because their Jaccard probability is tiny. Wilson's
    bound is used for non-zero observations to avoid a heavy statistics
    dependency in the extractor runtime.
    """

    if trials <= 0:
        return 1.0
    successes = max(0, min(int(successes), int(trials)))
    alpha = max(1e-12, 1.0 - float(confidence))
    if successes == 0:
        return 1.0 - alpha ** (1.0 / trials)
    if successes == trials:
        return 1.0
    proportion = successes / trials
    z_score = NormalDist().inv_cdf(confidence)
    z_squared = z_score * z_score
    denominator = 1.0 + z_squared / trials
    center = (proportion + z_squared / (2.0 * trials)) / denominator
    margin = (
        z_score
        * math.sqrt(proportion * (1.0 - proportion) / trials + z_squared / (4.0 * trials * trials))
        / denominator
    )
    return min(1.0, center + margin)


def _snowflake_minhash_profile_path(col: Dict, options: OverlapOptions) -> Path:
    db_ref = _safe_path_part(col.get("db_ref") or "unknown_db")
    key = hashlib.sha1(str(col.get("entity_name") or "").encode("utf-8")).hexdigest()
    profile_version = f"k{options.minhash_num_perm}_p{options.snowflake_minhash_value_partitions}"
    return _hash_index_root() / "snowflake_minhash" / profile_version / db_ref / f"{key}.json"


def _read_snowflake_minhash_profile(col: Dict, options: OverlapOptions) -> SnowflakeMinHashProfile | None:
    if _hash_index_force_rebuild():
        return None
    path = _snowflake_minhash_profile_path(col, options)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("method") != "snowflake_minhash_v4_partitioned_where":
            return None
        if int(payload.get("value_partitions") or 1) != options.snowflake_minhash_value_partitions:
            return None
        signature = tuple(int(item) for item in payload.get("signature") or [])
        cardinality = int(payload.get("cardinality") or 0)
        if len(signature) != options.minhash_num_perm or cardinality <= 0:
            return None
        lower_bound = int(payload.get("cardinality_lower_bound") or cardinality)
        upper_bound = int(payload.get("cardinality_upper_bound") or cardinality)
        return SnowflakeMinHashProfile(
            cardinality=cardinality,
            signature=signature,
            cardinality_lower_bound=lower_bound,
            cardinality_upper_bound=upper_bound,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_snowflake_minhash_profile(
    col: Dict,
    profile: SnowflakeMinHashProfile,
    options: OverlapOptions,
) -> None:
    path = _snowflake_minhash_profile_path(col, options)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "entity_name": col.get("entity_name"),
        "cardinality": profile.cardinality,
        "cardinality_lower_bound": profile.cardinality_lower_bound or profile.cardinality,
        "cardinality_upper_bound": profile.cardinality_upper_bound or profile.cardinality,
        "signature": list(profile.signature),
        "method": "snowflake_minhash_v4_partitioned_where",
        "value_partitions": options.snowflake_minhash_value_partitions,
    }, separators=(",", ":")) + "\n", encoding="utf-8")


def _detect_column_overlaps_hash_index(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
) -> List[Dict]:
    """Classify candidate columns with full local distinct-value hash indexes.

    Every physical column is scanned at most once to build a sorted uint64
    distinct-value hash array.  A logical column domain unions its member sets,
    then pair checks calculate the exact intersection cardinality of those
    unioned sets (up to a negligible 64-bit hash-collision probability).
    """

    if not candidates:
        return []

    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            available, reason = _value_database_available(cursor, dialect, candidates)
            if not available:
                logger.warning("  Skipping hash-index value check: %s", reason)
                return []
            indexes = _build_hash_indexes_for_candidates(cursor, dialect, candidates)
            index_sets = {
                ref: set(values)
                for ref, values in indexes.items()
                if values
            }
            domain_set_cache: dict[str, set[int]] = {}

            def value_set(col: Dict) -> set[int] | None:
                members = _domain_members(col)
                if not members:
                    return index_sets.get(col["entity_name"])
                ref = col["entity_name"]
                if ref not in domain_set_cache:
                    values: set[int] = set()
                    for member in members:
                        member_values = index_sets.get(member["entity_name"])
                        if member_values:
                            values.update(member_values)
                    domain_set_cache[ref] = values
                return domain_set_cache[ref]

            overlaps: list[Dict] = []
            for col1, col2 in candidates:
                left = value_set(col1)
                right = value_set(col2)
                if left is None or right is None:
                    continue
                if not left or not right:
                    continue
                shared_count = _hash_set_intersection_count(left, right)
                if shared_count == 0:
                    continue
                overlaps.append(_pair_overlap_payload(
                    col1,
                    col2,
                    _hash_index_overlap_stats(left, right, shared_count),
                ))
            overlaps.sort(key=lambda item: (-item["stats"]["overlap_coefficient"], item["from_ref"], item["to_ref"]))
            logger.info(
                "  Hash-index retained %s/%s value candidates",
                len(overlaps),
                len(candidates),
            )
            return overlaps
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Could not calculate hash value indexes: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return []


def _value_database_available(
    cursor,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
) -> tuple[bool, str]:
    if dialect != "snowflake" or not candidates:
        return True, ""
    db_ref = str(candidates[0][0].get("db_ref") or "").strip()
    if not db_ref:
        return True, ""
    try:
        cursor.execute(f"SELECT 1 FROM {_quote_identifier(db_ref, dialect)}.INFORMATION_SCHEMA.TABLES LIMIT 1")
        cursor.fetchone()
        return True, ""
    except Exception as exc:
        return False, f"{db_ref}: {exc}"


def _build_hash_indexes_for_candidates(
    cursor,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
) -> dict[str, array]:
    candidate_columns: dict[str, Dict] = {}
    for col1, col2 in candidates:
        candidate_columns[col1["entity_name"]] = col1
        candidate_columns[col2["entity_name"]] = col2

    physical_columns_for_availability: dict[str, Dict] = {}
    for col in candidate_columns.values():
        members = _domain_members(col)
        if members:
            for member in members:
                physical_columns_for_availability[member["entity_name"]] = member
        else:
            physical_columns_for_availability[col["entity_name"]] = col

    table_available: dict[str, bool] = {}
    table_skipped: dict[str, str] = {}
    for col in physical_columns_for_availability.values():
        table_key = _column_table_key(col)
        if table_key in table_available:
            continue
        available, reason = _hash_index_table_available(cursor, col, dialect)
        table_available[table_key] = available
        if not available:
            table_skipped[table_key] = reason

    for table_key, reason in sorted(table_skipped.items()):
        logger.warning("Skipping hash value indexes for inaccessible table %s: %s", table_key, reason)

    indexes: dict[str, array] = {}
    unavailable_tables = {table_key for table_key, available in table_available.items() if not available}
    index_columns = physical_columns_for_availability
    started = time.time()
    for index, col in enumerate(sorted(index_columns.values(), key=lambda item: item["entity_name"]), start=1):
        if not table_available.get(_column_table_key(col), True):
            continue
        try:
            indexes[col["entity_name"]] = _load_or_build_column_hash_index(
                cursor,
                col,
                dialect,
                unavailable_tables=unavailable_tables,
            )
        except Exception as exc:
            logger.warning("Failed to build hash value index for %s: %s", col["entity_name"], exc)
        if index % 100 == 0:
            logger.info(
                "  Built/loaded hash indexes for %s/%s columns (%.1fs)",
                index,
                len(index_columns),
                time.time() - started,
            )
    return indexes


def _column_table_key(col: Dict) -> str:
    return ".".join(
        part
        for part in (
            str(col.get("db_ref") or "").strip(),
            str(col.get("schema_name") or "").strip(),
            str(col.get("table_name") or "").strip(),
        )
        if part
    )


def _hash_index_table_available(cursor, col: Dict, dialect: str) -> tuple[bool, str]:
    try:
        cursor.execute(f"SELECT 1 FROM {_qualified_table_sql(col, dialect)} LIMIT 0")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _load_or_build_column_hash_index(
    cursor,
    col: Dict,
    dialect: str,
    *,
    unavailable_tables: set[str] | None = None,
) -> array:
    data_path, meta_path = _hash_index_paths(col)
    if data_path.exists() and not _hash_index_force_rebuild():
        try:
            return _read_hash_array(data_path)
        except Exception as exc:
            logger.debug("Could not read hash index %s: %s; rebuilding", data_path, exc)

    unavailable_tables = unavailable_tables or set()
    if _domain_members(col):
        values = _build_domain_hash_array(
            cursor,
            col,
            dialect,
            unavailable_tables=unavailable_tables,
        )
    else:
        if _column_table_key(col) in unavailable_tables:
            values = array("Q")
        else:
            values = _build_column_hash_array(cursor, col, dialect)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    _write_hash_array(data_path, values)
    meta_path.write_text(
        json.dumps(
            {
                "entity_name": col.get("entity_name"),
                "db_ref": col.get("db_ref"),
                "schema_name": col.get("schema_name"),
                "table_name": col.get("table_name"),
                "column": col.get("column"),
                "data_type": col.get("data_type"),
                "hash_count": len(values),
                "domain_member_count": len(_domain_members(col)),
                "domain_role": col.get("domain_role"),
                "domain_unit": col.get("domain_unit"),
                "method": "blake2b_64_sorted_distinct",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return values


def _build_domain_hash_array(
    cursor,
    col: Dict,
    dialect: str,
    *,
    unavailable_tables: set[str] | None = None,
) -> array:
    hashes: set[int] = set()
    unavailable_tables = unavailable_tables or set()
    for member in _domain_members(col):
        if _column_table_key(member) in unavailable_tables:
            continue
        try:
            hashes.update(
                _load_or_build_column_hash_index(
                    cursor,
                    member,
                    dialect,
                    unavailable_tables=unavailable_tables,
                )
            )
        except Exception as exc:
            logger.warning("Failed to build hash value index for domain member %s: %s", member.get("entity_name"), exc)
    return array("Q", sorted(hashes))


def _build_column_hash_array(cursor, col: Dict, dialect: str) -> array:
    hashes: set[int] = set()
    last_exc: Exception | None = None
    for column_sql in _column_sql_variants(col["column"], dialect):
        try:
            cursor.execute(_distinct_column_values_sql(col, dialect, column_sql=column_sql))
            break
        except Exception as exc:
            last_exc = exc
    else:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"No column SQL variants for {col.get('entity_name')}")
    while True:
        rows = cursor.fetchmany(HASH_INDEX_FETCH_SIZE)
        if not rows:
            break
        for row in rows:
            value_hash = _value_hash64(_row_value(row, 0))
            if value_hash is not None:
                hashes.add(value_hash)
    return array("Q", sorted(hashes))


def _column_sql_variants(column_name: str, dialect: str) -> list[str]:
    exact = _quote_identifier(column_name, dialect)
    if dialect != "snowflake":
        return [exact]
    upper = _quote_identifier(str(column_name).upper(), dialect)
    if upper == exact:
        return [exact]
    return [exact, upper]


def _hash_index_paths(col: Dict) -> tuple[Path, Path]:
    root = _hash_index_root()
    db_ref = _safe_path_part(col.get("db_ref") or "unknown_db")
    column_key = hashlib.sha1(str(col.get("entity_name") or "").encode("utf-8")).hexdigest()
    data_path = root / db_ref / f"{column_key}.u64"
    return data_path, data_path.with_suffix(".json")


def _hash_index_root() -> Path:
    raw = os.environ.get("PONTIS_VALUE_INDEX_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "workspace" / "baselines" / "pontis" / "value_index"


def _hash_index_force_rebuild() -> bool:
    raw = os.environ.get("PONTIS_VALUE_INDEX_REBUILD", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_path_part(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "_", text).strip("_")
    return text or "unknown"


def _read_hash_array(path: Path) -> array:
    values = array("Q")
    size = path.stat().st_size
    if size == 0:
        return values
    if size % values.itemsize != 0:
        raise ValueError(f"Invalid uint64 hash index size: {path}")
    with path.open("rb") as fh:
        values.fromfile(fh, size // values.itemsize)
    return values


def _write_hash_array(path: Path, values: array) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        values.tofile(fh)
    tmp.replace(path)


def _value_hash64(value: Any) -> int | None:
    normalized = _normalize_value(value)
    if normalized == "":
        return None
    digest = hashlib.blake2b(normalized.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def _hash_sets_intersect(left: set[int], right: set[int]) -> bool:
    if len(left) > len(right):
        left, right = right, left
    return not left.isdisjoint(right)


def _hash_set_intersection_count(left: set[int], right: set[int]) -> int:
    if len(left) > len(right):
        left, right = right, left
    return sum(value in right for value in left)


def _sorted_hash_arrays_intersect(left: array, right: array) -> bool:
    if len(left) > len(right):
        left, right = right, left
    i = 0
    j = 0
    len_left = len(left)
    len_right = len(right)
    while i < len_left and j < len_right:
        lv = left[i]
        rv = right[j]
        if lv == rv:
            return True
        if lv < rv:
            i += 1
        else:
            j += 1
    return False


def _hash_index_overlap_stats(
    left: set[int] | array,
    right: set[int] | array,
    card_overlap: int,
) -> Dict:
    card_1 = len(left)
    card_2 = len(right)
    return {
        "overlap_coefficient": round(card_overlap / min(card_1, card_2), 6),
        "method": "hash_index_exact_distinct_union",
    }


def _detect_column_overlaps_minhash(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    """Use per-column MinHash signatures before optional exact SQL verification."""

    if not candidates:
        return []

    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            profiles = _build_minhash_profiles(cursor, dialect, candidates, options)
            retained = _filter_candidates_by_minhash(candidates, profiles, options)
            logger.info(
                "  MinHash retained %s/%s value candidates (method=%s, num_perm=%s)",
                len(retained),
                len(candidates),
                options.value_match_method,
                options.minhash_num_perm,
            )
            if options.value_match_method == "minhash":
                overlaps = [
                    _pair_overlap_payload(col1, col2, estimated)
                    for col1, col2, estimated in retained
                ]
                overlaps.sort(key=lambda item: (-item["stats"]["overlap_coefficient"], item["from_ref"], item["to_ref"]))
                return overlaps

            verify_candidates = [(col1, col2) for col1, col2, _estimated in retained]
            if (
                options.minhash_max_sql_verify_pairs > 0
                and len(verify_candidates) > options.minhash_max_sql_verify_pairs
            ):
                retained = sorted(
                    retained,
                    key=lambda item: (
                        -item[2]["overlap_coefficient"],
                        item[0]["entity_name"],
                        item[1]["entity_name"],
                    ),
                )[: options.minhash_max_sql_verify_pairs]
                verify_candidates = [(col1, col2) for col1, col2, _estimated in retained]
                logger.info(
                    "  MinHash SQL verification capped to %s pairs",
                    options.minhash_max_sql_verify_pairs,
                )
            return _detect_column_overlaps_sql_cursor(cursor, dialect, verify_candidates, options)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Could not calculate MinHash profiles: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return []


def _build_minhash_profiles(
    cursor,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> dict[str, MinHashProfile]:
    columns: dict[str, Dict] = {}
    for col1, col2 in candidates:
        columns[col1["entity_name"]] = col1
        columns[col2["entity_name"]] = col2

    profiles: dict[str, MinHashProfile] = {}
    for col in sorted(columns.values(), key=lambda item: item["entity_name"]):
        profiles[col["entity_name"]] = _build_column_minhash_profile(cursor, col, dialect, options)
    return profiles


def _build_column_minhash_profile(
    cursor,
    col: Dict,
    dialect: str,
    options: OverlapOptions,
) -> MinHashProfile:
    signature = [MINHASH_MODULUS] * options.minhash_num_perm
    cardinality = 0
    sample_values: list[str] = []
    cursor.execute(_distinct_column_values_sql(col, dialect))
    while True:
        rows = cursor.fetchmany(10000)
        if not rows:
            break
        for row in rows:
            value = _normalize_value(_row_value(row, 0))
            if not value:
                continue
            cardinality += 1
            if len(sample_values) < INTERSECTION_SAMPLE_LIMIT:
                sample_values.append(value)
            _update_minhash_signature(signature, value)
    return MinHashProfile(cardinality=cardinality, signature=tuple(signature), sample_values=sample_values)


def _distinct_column_values_sql(col: Dict, dialect: str, *, column_sql: str | None = None) -> str:
    table = _qualified_table_sql(col, dialect)
    column = column_sql or _quote_identifier(col["column"], dialect)
    return f"""
SELECT DISTINCT {column} AS v
FROM {table}
WHERE {column} IS NOT NULL
"""


def _update_minhash_signature(signature: list[int], value: str) -> None:
    h1, h2 = _minhash_base_hashes(value)
    for index in range(len(signature)):
        candidate = (h1 + (index + 1) * h2) % MINHASH_MODULUS
        if candidate < signature[index]:
            signature[index] = candidate


def _minhash_base_hashes(value: str) -> tuple[int, int]:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest()
    h1 = int.from_bytes(digest[:8], "big") % MINHASH_MODULUS
    h2 = int.from_bytes(digest[8:], "big") % MINHASH_MODULUS
    return h1, h2 or 1


def _filter_candidates_by_minhash(
    candidates: list[tuple[Dict, Dict]],
    profiles: dict[str, MinHashProfile],
    options: OverlapOptions,
) -> list[tuple[Dict, Dict, Dict]]:
    retained: list[tuple[Dict, Dict, Dict]] = []
    for col1, col2 in candidates:
        left = profiles.get(col1["entity_name"])
        right = profiles.get(col2["entity_name"])
        if not left or not right:
            continue
        estimated = _estimate_overlap_from_minhash(col1, col2, left, right, options)
        if estimated is None:
            continue
        retained.append((col1, col2, estimated))
    return retained


def _estimate_overlap_from_minhash(
    col1: Dict,
    col2: Dict,
    left: MinHashProfile,
    right: MinHashProfile,
    options: OverlapOptions,
) -> Optional[Dict]:
    if left.cardinality <= 0 or right.cardinality <= 0:
        return None
    matching_hashes = sum(1 for a, b in zip(left.signature, right.signature) if a == b)
    if matching_hashes < options.minhash_min_matching_hashes:
        return None
    jaccard = matching_hashes / max(1, min(len(left.signature), len(right.signature)))
    if jaccard < options.minhash_jaccard_threshold:
        return None

    estimated_overlap = int(round((jaccard * (left.cardinality + right.cardinality)) / (1.0 + jaccard)))
    estimated_overlap = min(max(estimated_overlap, 1), min(left.cardinality, right.cardinality))
    if estimated_overlap <= 0:
        return None
    return {
        "overlap_coefficient": round(estimated_overlap / min(left.cardinality, right.cardinality), 6),
        "matching_hashes": matching_hashes,
        "estimated": True,
    }


def _detect_column_overlaps_sample_bloom(
    db_connect,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> List[Dict]:
    """Estimate containment-style value overlaps with bottom-k + Bloom filters.

    This targets the overlap coefficient. For each candidate pair, probe the
    larger side's Bloom filter with a uniform bottom-k sample from the smaller
    side. The result is a fast approximate candidate set; callers can choose
    sample_bloom_then_sql when exact confirmation is required.
    """

    if not candidates:
        return []

    cached_profiles = _build_sample_bloom_profiles_from_cache(candidates, options)
    if cached_profiles is not None:
        logger.info("  Sample-Bloom using cached profiles without opening database connection")
        return _sample_bloom_overlaps_from_profiles(candidates, cached_profiles, options)

    conn = None
    try:
        conn = _open_db_connection(db_connect, readonly=True)
        cursor = conn.cursor()
        try:
            available, reason = _value_database_available(cursor, dialect, candidates)
            if not available:
                logger.warning("  Skipping sample-bloom value check: %s", reason)
                return []
            profiles = _build_sample_bloom_profiles(cursor, dialect, candidates, options)
            retained, overlaps = _sample_bloom_retained(candidates, profiles, options)
            verify_candidates = [(col1, col2) for col1, col2, _estimated in retained]
            if options.value_match_method == "sample_bloom":
                return overlaps
            if _has_column_domain_candidates(verify_candidates):
                logger.warning(
                    "  Sample-Bloom SQL verification does not support column-domain candidates; returning estimates"
                )
                return overlaps
            if (
                options.minhash_max_sql_verify_pairs > 0
                and len(verify_candidates) > options.minhash_max_sql_verify_pairs
            ):
                retained = sorted(
                    retained,
                    key=lambda item: (
                        -item[2]["overlap_coefficient"],
                        -item[2]["sample_hits"],
                        item[0]["entity_name"],
                        item[1]["entity_name"],
                    ),
                )[: options.minhash_max_sql_verify_pairs]
                verify_candidates = [(col1, col2) for col1, col2, _estimated in retained]
                logger.info(
                    "  Sample-Bloom SQL verification capped to %s pairs",
                    options.minhash_max_sql_verify_pairs,
                )
            return _detect_column_overlaps_sql_cursor(cursor, dialect, verify_candidates, options)
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Could not calculate Sample-Bloom profiles: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return []


def _build_sample_bloom_profiles(
    cursor,
    dialect: str,
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> dict[str, SampleBloomProfile]:
    physical_columns, domain_columns = _sample_bloom_profile_columns(candidates, options=options)
    table_available: dict[str, bool] = {}
    table_skipped: dict[str, str] = {}
    profiles: dict[str, SampleBloomProfile] = {}
    missing_columns: dict[str, Dict] = {}
    started = time.time()
    for index, col in enumerate(sorted(physical_columns.values(), key=lambda item: item["entity_name"]), start=1):
        cached = _read_cached_column_sample_bloom_profile(col, options)
        if cached is not None:
            profiles[col["entity_name"]] = cached
            continue
        missing_columns[col["entity_name"]] = col

    if dialect == "snowflake" and options.sample_bloom_sample_rows > 0:
        by_table: dict[str, list[Dict]] = defaultdict(list)
        for col in missing_columns.values():
            by_table[_column_table_key(col)].append(col)
        for table_key, cols in sorted(by_table.items()):
            try:
                available, reason = _hash_index_table_available(cursor, cols[0], dialect)
                if not available:
                    logger.warning("Skipping sample-bloom profile for inaccessible table %s: %s", table_key, reason)
                    continue
                table_profiles = _build_table_sample_bloom_profiles(cursor, cols, dialect, options)
                profiles.update(table_profiles)
            except Exception as exc:
                logger.warning("Failed to build table sample-bloom profiles for %s: %s", table_key, exc)
            logger.info(
                "  Built/loaded sample-bloom profiles for table %s (%s/%s physical columns, %.1fs)",
                table_key,
                len(profiles),
                len(physical_columns),
                time.time() - started,
            )
    else:
        for index, col in enumerate(sorted(missing_columns.values(), key=lambda item: item["entity_name"]), start=1):
            table_key = _column_table_key(col)
            if table_key not in table_available:
                available, reason = _hash_index_table_available(cursor, col, dialect)
                table_available[table_key] = available
                if not available:
                    table_skipped[table_key] = reason
                    logger.warning("Skipping sample-bloom profile for inaccessible table %s: %s", table_key, reason)
            if not table_available.get(_column_table_key(col), True):
                continue
            try:
                profiles[col["entity_name"]] = _load_or_build_column_sample_bloom_profile(cursor, col, dialect, options)
            except Exception as exc:
                logger.warning("Failed to build sample-bloom profile for %s: %s", col["entity_name"], exc)
            if index % 100 == 0:
                logger.info(
                    "  Built sample-bloom profiles for %s/%s missing physical columns (%.1fs)",
                    index,
                    len(missing_columns),
                    time.time() - started,
                )

    for ref, col in sorted(domain_columns.items()):
        profile = _build_domain_sample_bloom_profile(col, profiles, options)
        if profile is not None:
            profiles[ref] = profile

    return profiles


def _build_sample_bloom_profiles_from_cache(
    candidates: list[tuple[Dict, Dict]],
    options: OverlapOptions,
) -> dict[str, SampleBloomProfile] | None:
    physical_columns, domain_columns = _sample_bloom_profile_columns(candidates, options=options)
    profiles: dict[str, SampleBloomProfile] = {}
    for col in physical_columns.values():
        profile = _read_cached_column_sample_bloom_profile(col, options)
        if profile is None:
            return None
        profiles[col["entity_name"]] = profile
    for ref, col in sorted(domain_columns.items()):
        profile = _build_domain_sample_bloom_profile(col, profiles, options)
        if profile is None:
            return None
        profiles[ref] = profile
    return profiles


def _sample_bloom_profile_columns(
    candidates: list[tuple[Dict, Dict]],
    *,
    options: OverlapOptions | None = None,
) -> tuple[dict[str, Dict], dict[str, Dict]]:
    candidate_columns: dict[str, Dict] = {}
    for col1, col2 in candidates:
        candidate_columns[col1["entity_name"]] = col1
        candidate_columns[col2["entity_name"]] = col2

    physical_columns: dict[str, Dict] = {}
    domain_columns: dict[str, Dict] = {}
    for col in candidate_columns.values():
        members = _domain_members(col)
        if members:
            domain_columns[col["entity_name"]] = col
            for member in _sample_bloom_domain_members(col, options):
                physical_columns[member["entity_name"]] = member
        else:
            physical_columns[col["entity_name"]] = col
    return physical_columns, domain_columns


def _sample_bloom_domain_members(
    col: Dict,
    options: OverlapOptions | None,
) -> list[Dict]:
    """Choose a bounded, evenly spaced sample of a logical domain's members.

    Large Spider2 table groups can contain thousands of physical shards.  A
    Bloom profile is already a value sample, so sampling every shard would turn
    a compact estimator into hundreds of thousands of remote scans.  The
    selected members are spread deterministically over the sorted group, which
    gives every position in a date/partition family representation.
    """

    members = _domain_members(col)
    limit = int(options.sample_bloom_max_domain_members or 0) if options else 0
    if limit <= 0 or len(members) <= limit:
        return members
    if limit == 1:
        return [members[0]]
    indices = {
        round(index * (len(members) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [members[index] for index in sorted(indices)]


def _sample_bloom_overlaps_from_profiles(
    candidates: list[tuple[Dict, Dict]],
    profiles: dict[str, SampleBloomProfile],
    options: OverlapOptions,
) -> list[Dict]:
    _retained, overlaps = _sample_bloom_retained(candidates, profiles, options)
    return overlaps


def _sample_bloom_retained(
    candidates: list[tuple[Dict, Dict]],
    profiles: dict[str, SampleBloomProfile],
    options: OverlapOptions,
) -> tuple[list[tuple[Dict, Dict, Dict]], list[Dict]]:
    retained: list[tuple[Dict, Dict, Dict]] = []
    for col1, col2 in candidates:
        left = profiles.get(col1["entity_name"])
        right = profiles.get(col2["entity_name"])
        if not left or not right:
            continue
        estimated = _estimate_overlap_from_sample_bloom(col1, col2, left, right, profiles, options)
        if estimated is None:
            continue
        retained.append((col1, col2, estimated))

    logger.info(
        "  Sample-Bloom retained %s/%s value candidates (sample_size=%s, min_hits=%s)",
        len(retained),
        len(candidates),
        options.sample_bloom_sample_size,
        options.sample_bloom_min_hits,
    )
    overlaps = [
        _pair_overlap_payload(col1, col2, estimated)
        for col1, col2, estimated in retained
    ]
    overlaps.sort(key=lambda item: (
        -item["stats"]["overlap_coefficient"],
        item["from_ref"],
        item["to_ref"],
    ))
    return retained, overlaps


def _build_column_sample_bloom_profile(
    cursor,
    col: Dict,
    dialect: str,
    options: OverlapOptions,
) -> SampleBloomProfile:
    profile = _new_sample_bloom_profile(options)
    sample_heap: list[int] = []
    last_exc: Exception | None = None
    for column_sql in _column_sql_variants(col["column"], dialect):
        try:
            if dialect == "snowflake":
                cursor.execute(_distinct_column_hashes_sql(col, dialect, column_sql=column_sql, options=options))
            else:
                cursor.execute(_distinct_column_values_sql(col, dialect, column_sql=column_sql))
            break
        except Exception as exc:
            last_exc = exc
    else:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"No column SQL variants for {col.get('entity_name')}")

    cardinality = 0
    while True:
        rows = cursor.fetchmany(HASH_INDEX_FETCH_SIZE)
        if not rows:
            break
        for row in rows:
            value_hash = (
                _snowflake_hash_to_u64(_row_value(row, 0))
                if dialect == "snowflake"
                else _value_hash64(_row_value(row, 0))
            )
            if value_hash is None:
                continue
            cardinality += 1
            _sample_bloom_add(profile, value_hash, options)
            _bottomk_offer(sample_heap, value_hash, options.sample_bloom_sample_size)

    profile.cardinality = cardinality
    profile.sample_hashes = tuple(sorted(-item for item in sample_heap))
    return profile


def _build_table_sample_bloom_profiles(
    cursor,
    cols: list[Dict],
    dialect: str,
    options: OverlapOptions,
) -> dict[str, SampleBloomProfile]:
    if not cols:
        return {}
    profiles = {col["entity_name"]: _new_sample_bloom_profile(options) for col in cols}
    sample_heaps: dict[str, list[int]] = {col["entity_name"]: [] for col in cols}
    last_exc: Exception | None = None
    variant_sets = _table_column_variant_sets(cols, dialect)
    for variant_index in range(max(len(variants) for variants in variant_sets.values())):
        column_sql_by_ref = {
            col["entity_name"]: variants[min(variant_index, len(variants) - 1)]
            for col in cols
            for variants in [variant_sets[col["entity_name"]]]
        }
        try:
            cursor.execute(_table_sample_bloom_hashes_sql(cols, dialect, options, column_sql_by_ref))
            break
        except Exception as exc:
            last_exc = exc
    else:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No table sample-bloom SQL variants")

    while True:
        rows = cursor.fetchmany(HASH_INDEX_FETCH_SIZE)
        if not rows:
            break
        for row in rows:
            ref = str(_row_value(row, 0) or "")
            value_hash = _snowflake_hash_to_u64(_row_value(row, 1))
            profile = profiles.get(ref)
            if profile is None or value_hash is None:
                continue
            _sample_bloom_profile_add_hash(profile, sample_heaps[ref], value_hash, options)

    for col in cols:
        ref = col["entity_name"]
        profile = profiles[ref]
        profile.sample_hashes = tuple(sorted(-item for item in sample_heaps[ref]))
        path = _sample_bloom_profile_path(col)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_sample_bloom_profile(path, profile, col, options)
    return profiles


def _table_column_variant_sets(cols: list[Dict], dialect: str) -> dict[str, list[str]]:
    return {
        col["entity_name"]: _column_sql_variants(col["column"], dialect)
        for col in cols
    }


def _table_sample_bloom_hashes_sql(
    cols: list[Dict],
    dialect: str,
    options: OverlapOptions,
    column_sql_by_ref: dict[str, str],
) -> str:
    table = _qualified_table_sql(cols[0], dialect)
    source_sql = _sample_bloom_source_sql(table, dialect, options)
    selects = []
    for col in cols:
        ref_literal = "'" + str(col["entity_name"]).replace("'", "''") + "'"
        column_sql = column_sql_by_ref[col["entity_name"]]
        normalized = f"LOWER(TRIM(TO_VARCHAR({column_sql})))"
        selects.append(
            f"SELECT {ref_literal} AS ref, HASH({normalized}) AS h "
            f"FROM sampled WHERE {column_sql} IS NOT NULL AND {normalized} <> ''"
        )
    union_sql = "\nUNION ALL\n".join(selects)
    return f"""
WITH sampled AS (
  SELECT *
  FROM {source_sql}
)
SELECT DISTINCT ref, h
FROM (
{union_sql}
)
WHERE h IS NOT NULL
"""


def _sample_bloom_source_sql(table_sql: str, dialect: str, options: OverlapOptions) -> str:
    sample_rows = int(options.sample_bloom_sample_rows or 0)
    if dialect == "snowflake" and sample_rows > 0:
        return f"(SELECT * FROM {table_sql} LIMIT {sample_rows})"
    return table_sql


def _sample_bloom_profile_add_hash(
    profile: SampleBloomProfile,
    sample_heap: list[int],
    value_hash: int,
    options: OverlapOptions,
) -> None:
    profile.cardinality += 1
    _sample_bloom_add(profile, value_hash, options)
    _bottomk_offer(sample_heap, value_hash, options.sample_bloom_sample_size)


def _load_or_build_column_sample_bloom_profile(
    cursor,
    col: Dict,
    dialect: str,
    options: OverlapOptions,
) -> SampleBloomProfile:
    profile = _read_cached_column_sample_bloom_profile(col, options)
    if profile is not None:
        return profile

    profile = _build_column_sample_bloom_profile(cursor, col, dialect, options)
    path = _sample_bloom_profile_path(col)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_sample_bloom_profile(path, profile, col, options)
    return profile


def _read_cached_column_sample_bloom_profile(
    col: Dict,
    options: OverlapOptions,
) -> SampleBloomProfile | None:
    if _hash_index_force_rebuild():
        return None
    path = _sample_bloom_profile_path(col)
    if not path.exists():
        return None
    try:
        return _read_sample_bloom_profile(path, col, options)
    except Exception as exc:
        logger.debug("Could not read sample-bloom profile %s: %s; rebuilding", path, exc)
        return None


def _build_domain_sample_bloom_profile(
    col: Dict,
    profiles: dict[str, SampleBloomProfile],
    options: OverlapOptions,
) -> SampleBloomProfile | None:
    all_members = _domain_members(col)
    selected_members = _sample_bloom_domain_members(col, options)
    member_refs = tuple(
        member["entity_name"]
        for member in selected_members
        if member.get("entity_name") in profiles
    )
    if not member_refs:
        return None
    observed_cardinality = sum(profiles[ref].cardinality for ref in member_refs)
    selection_scale = len(all_members) / len(selected_members) if selected_members else 1.0
    cardinality = round(observed_cardinality * selection_scale)
    sample_heap: list[int] = []
    seen_samples: set[int] = set()
    for ref in member_refs:
        for value_hash in profiles[ref].sample_hashes:
            if value_hash in seen_samples:
                continue
            seen_samples.add(value_hash)
            _bottomk_offer(sample_heap, value_hash, options.sample_bloom_sample_size)
    layers = _merge_sample_bloom_layers([profiles[ref] for ref in member_refs])
    return SampleBloomProfile(
        cardinality=cardinality,
        sample_hashes=tuple(sorted(-item for item in sample_heap)),
        layers=layers,
        member_refs=() if layers else member_refs,
    )


def _merge_sample_bloom_layers(
    profiles: list[SampleBloomProfile],
) -> tuple[BloomLayer, ...]:
    """Union compatible physical Bloom filters into one domain filter.

    Bloom insertion is bitwise OR.  Materializing the union avoids recursively
    probing every physical member for every sampled value-pair.
    """

    if not profiles:
        return ()
    max_layers = max(len(profile.layers) for profile in profiles)
    merged: list[BloomLayer] = []
    for index in range(max_layers):
        source_layers = [profile.layers[index] for profile in profiles if len(profile.layers) > index]
        first = source_layers[0]
        if any(
            layer.bit_count != first.bit_count
            or layer.hash_count != first.hash_count
            or layer.capacity != first.capacity
            for layer in source_layers
        ):
            # This only occurs for differently grown scalable filters.  Keep
            # the domain as member-backed in that rare case rather than risk a
            # malformed bitmap union.
            return ()
        bits = bytearray(first.bits)
        for layer in source_layers[1:]:
            for offset, value in enumerate(layer.bits):
                bits[offset] |= value
        merged.append(BloomLayer(
            bit_count=first.bit_count,
            hash_count=first.hash_count,
            capacity=first.capacity,
            count=sum(layer.count for layer in source_layers),
            bits=bits,
        ))
    return tuple(merged)


def _sample_bloom_profile_path(col: Dict) -> Path:
    db_ref = _safe_path_part(col.get("db_ref") or "unknown_db")
    column_key = hashlib.sha1(str(col.get("entity_name") or "").encode("utf-8")).hexdigest()
    return _hash_index_root() / "sample_bloom" / db_ref / f"{column_key}.json"


def _sample_bloom_meta(col: Dict, options: OverlapOptions) -> dict:
    return {
        "entity_name": col.get("entity_name"),
        "db_ref": col.get("db_ref"),
        "schema_name": col.get("schema_name"),
        "table_name": col.get("table_name"),
        "column": col.get("column"),
        "data_type": col.get("data_type"),
        "method": "bottomk_sample_scalable_bloom_v5_full_domain",
        "sample_size": options.sample_bloom_sample_size,
        "false_positive_rate": options.sample_bloom_false_positive_rate,
        "initial_capacity": options.sample_bloom_initial_capacity,
        "growth_factor": options.sample_bloom_growth_factor,
        "sample_rows": options.sample_bloom_sample_rows,
    }


def _write_sample_bloom_profile(
    path: Path,
    profile: SampleBloomProfile,
    col: Dict,
    options: OverlapOptions,
) -> None:
    payload = _sample_bloom_meta(col, options)
    payload.update({
        "cardinality": profile.cardinality,
        "sample_hashes": list(profile.sample_hashes),
        "layers": [
            {
                "bit_count": layer.bit_count,
                "hash_count": layer.hash_count,
                "capacity": layer.capacity,
                "count": layer.count,
                "bits": base64.b64encode(bytes(layer.bits)).decode("ascii"),
            }
            for layer in profile.layers
        ],
    })
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_sample_bloom_profile(
    path: Path,
    col: Dict,
    options: OverlapOptions,
) -> SampleBloomProfile | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = _sample_bloom_meta(col, options)
    for key, value in expected.items():
        if payload.get(key) != value:
            return None
    layers = tuple(
        BloomLayer(
            bit_count=int(item["bit_count"]),
            hash_count=int(item["hash_count"]),
            capacity=int(item["capacity"]),
            count=int(item["count"]),
            bits=bytearray(base64.b64decode(item["bits"])),
        )
        for item in payload.get("layers") or []
    )
    return SampleBloomProfile(
        cardinality=int(payload.get("cardinality") or 0),
        sample_hashes=tuple(int(value) for value in payload.get("sample_hashes") or []),
        layers=layers,
    )


def _distinct_column_hashes_sql(
    col: Dict,
    dialect: str,
    *,
    column_sql: str,
    options: OverlapOptions,
) -> str:
    source = _sample_bloom_source_sql(_qualified_table_sql(col, dialect), dialect, options)
    if options.sample_bloom_sample_rows > 0:
        source = f"{source} AS pontis_sample"
    column_ref = column_sql
    normalized = f"LOWER(TRIM(TO_VARCHAR({column_ref})))"
    return f"""
SELECT DISTINCT HASH({normalized}) AS h
FROM {source}
WHERE {column_ref} IS NOT NULL
  AND {normalized} <> ''
"""


def _snowflake_hash_to_u64(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value) & ((1 << 64) - 1)
    except (TypeError, ValueError):
        return None


def _new_sample_bloom_profile(options: OverlapOptions) -> SampleBloomProfile:
    return SampleBloomProfile(
        cardinality=0,
        sample_hashes=(),
        layers=(_new_bloom_layer(options.sample_bloom_initial_capacity, options.sample_bloom_false_positive_rate),),
    )


def _new_bloom_layer(capacity: int, false_positive_rate: float) -> BloomLayer:
    capacity = max(1, int(capacity))
    fp = min(0.5, max(0.000001, float(false_positive_rate)))
    bit_count = max(8, int(math.ceil(-(capacity * math.log(fp)) / (math.log(2) ** 2))))
    hash_count = max(1, int(round((bit_count / capacity) * math.log(2))))
    return BloomLayer(
        bit_count=bit_count,
        hash_count=hash_count,
        capacity=capacity,
        count=0,
        bits=bytearray((bit_count + 7) // 8),
    )


def _sample_bloom_add(profile: SampleBloomProfile, value_hash: int, options: OverlapOptions) -> None:
    layers = list(profile.layers)
    if not layers:
        layers.append(_new_bloom_layer(options.sample_bloom_initial_capacity, options.sample_bloom_false_positive_rate))
    current = layers[-1]
    if current.count >= current.capacity:
        next_capacity = current.capacity * options.sample_bloom_growth_factor
        current = _new_bloom_layer(next_capacity, options.sample_bloom_false_positive_rate)
        layers.append(current)
        profile.layers = tuple(layers)
    _bloom_layer_add(current, value_hash)
    current.count += 1


def _bloom_layer_add(layer: BloomLayer, value_hash: int) -> None:
    for position in _bloom_positions(value_hash, layer.bit_count, layer.hash_count):
        layer.bits[position // 8] |= 1 << (position % 8)


def _profile_might_contain(
    profile: SampleBloomProfile,
    value_hash: int,
    profiles: dict[str, SampleBloomProfile],
) -> bool:
    if profile.member_refs:
        return any(
            _profile_might_contain(profiles[ref], value_hash, profiles)
            for ref in profile.member_refs
            if ref in profiles
        )
    return any(_bloom_layer_contains(layer, value_hash) for layer in profile.layers)


def _bloom_layer_contains(layer: BloomLayer, value_hash: int) -> bool:
    for position in _bloom_positions(value_hash, layer.bit_count, layer.hash_count):
        if not (layer.bits[position // 8] & (1 << (position % 8))):
            return False
    return True


def _bloom_positions(value_hash: int, bit_count: int, hash_count: int) -> Iterable[int]:
    h1 = value_hash & ((1 << 64) - 1)
    h2 = _mix_hash64(value_hash)
    if h2 == 0:
        h2 = 0x9E3779B97F4A7C15
    for index in range(hash_count):
        yield (h1 + index * h2) % bit_count


def _mix_hash64(value_hash: int) -> int:
    value = (value_hash ^ (value_hash >> 30)) * 0xBF58476D1CE4E5B9
    value &= (1 << 64) - 1
    value = (value ^ (value >> 27)) * 0x94D049BB133111EB
    value &= (1 << 64) - 1
    return value ^ (value >> 31)


def _bottomk_offer(heap: list[int], value_hash: int, sample_size: int) -> None:
    item = -value_hash
    if len(heap) < sample_size:
        heapq.heappush(heap, item)
    elif value_hash < -heap[0]:
        heapq.heapreplace(heap, item)


def _estimate_overlap_from_sample_bloom(
    col1: Dict,
    col2: Dict,
    left: SampleBloomProfile,
    right: SampleBloomProfile,
    profiles: dict[str, SampleBloomProfile],
    options: OverlapOptions,
) -> Optional[Dict]:
    if left.cardinality <= 0 or right.cardinality <= 0:
        return None
    if left.cardinality <= right.cardinality:
        small, large = left, right
        small_is_left = True
    else:
        small, large = right, left
        small_is_left = False
    if not small.sample_hashes:
        return None

    sample_hits = sum(1 for value_hash in small.sample_hashes if _profile_might_contain(large, value_hash, profiles))
    if sample_hits < options.sample_bloom_min_hits:
        return None

    overlap_coefficient = sample_hits / len(small.sample_hashes)
    return {
        "overlap_coefficient": round(overlap_coefficient, 6),
        "sample_hits": sample_hits,
        "sample_size": len(small.sample_hashes),
        "sample_side": "left" if small_is_left else "right",
        "estimated": True,
        "method": "sample_bloom",
    }


def _open_db_connection(db_connect, *, readonly: bool = True):
    try:
        return db_connect(readonly=readonly)
    except TypeError:
        return db_connect()


def _calculate_overlap_sql(
    cursor,
    col1: Dict,
    col2: Dict,
    dialect: str,
    options: OverlapOptions,
) -> Optional[Dict]:
    try:
        query = _overlap_count_sql(col1, col2, dialect)
        cursor.execute(query)
        row = cursor.fetchone()
        if not row:
            return None
        card_1 = int(_row_value(row, 0) or 0)
        card_2 = int(_row_value(row, 1) or 0)
        card_overlap = int(_row_value(row, 2) or 0)
        if card_overlap == 0:
            return None

        intersection_sample: list[str] = []
        if _needs_intersection_sample(card_1, card_2, card_overlap):
            cursor.execute(_overlap_sample_sql(col1, col2, dialect))
            intersection_sample = [
                _normalize_value(_row_value(sample_row, 0))
                for sample_row in cursor.fetchall()
            ]
        if _is_disabled_overlap_stats(
            col1,
            col2,
            card_1,
            card_2,
            card_overlap,
            intersection_sample,
        ):
            return None

        union = card_1 + card_2 - card_overlap
        return {
            "card_overlap": card_overlap,
            "cardinality_A": card_1,
            "cardinality_B": card_2,
            "jaccard": round(card_overlap / union, 4) if union else 0.0,
            "coverage_A_in_B": round(card_overlap / card_1, 4) if card_1 else 0.0,
            "coverage_B_in_A": round(card_overlap / card_2, 4) if card_2 else 0.0,
            "overlap_coefficient": round(card_overlap / min(card_1, card_2), 6),
            "method": "sql_exact_distinct",
        }
    except Exception as e:
        logger.debug("Could not calculate SQL overlap for %s <-> %s: %s", col1["entity_name"], col2["entity_name"], e)
        return None


def _overlap_count_sql(col1: Dict, col2: Dict, dialect: str) -> str:
    left_table = _qualified_table_sql(col1, dialect)
    right_table = _qualified_table_sql(col2, dialect)
    left_col = _quote_identifier(col1["column"], dialect)
    right_col = _quote_identifier(col2["column"], dialect)
    join_condition = _exact_value_join_condition(dialect)
    return f"""
WITH
  pontis_overlap_left AS (
    SELECT DISTINCT {left_col} AS v
    FROM {left_table}
    WHERE {left_col} IS NOT NULL
  ),
  pontis_overlap_right AS (
    SELECT DISTINCT {right_col} AS v
    FROM {right_table}
    WHERE {right_col} IS NOT NULL
  ),
  pontis_overlap_intersection AS (
    SELECT pontis_overlap_left.v AS v
    FROM pontis_overlap_left
    INNER JOIN pontis_overlap_right ON {join_condition}
  )
SELECT
  (SELECT COUNT(*) FROM pontis_overlap_left) AS cardinality_a,
  (SELECT COUNT(*) FROM pontis_overlap_right) AS cardinality_b,
  (SELECT COUNT(*) FROM pontis_overlap_intersection) AS card_overlap
"""


def _overlap_sample_sql(col1: Dict, col2: Dict, dialect: str) -> str:
    left_table = _qualified_table_sql(col1, dialect)
    right_table = _qualified_table_sql(col2, dialect)
    left_col = _quote_identifier(col1["column"], dialect)
    right_col = _quote_identifier(col2["column"], dialect)
    join_condition = _exact_value_join_condition(dialect)
    return f"""
WITH
  pontis_overlap_left AS (
    SELECT DISTINCT {left_col} AS v
    FROM {left_table}
    WHERE {left_col} IS NOT NULL
  ),
  pontis_overlap_right AS (
    SELECT DISTINCT {right_col} AS v
    FROM {right_table}
    WHERE {right_col} IS NOT NULL
  )
SELECT pontis_overlap_left.v AS v
FROM pontis_overlap_left
INNER JOIN pontis_overlap_right ON {join_condition}
LIMIT {INTERSECTION_SAMPLE_LIMIT}
"""


def _exact_value_join_condition(dialect: str) -> str:
    equality = "pontis_overlap_left.v = pontis_overlap_right.v"
    if dialect not in {"sqlite", "duckdb"}:
        return equality
    # The legacy extractor compared Python sets: integer and real values may
    # compare equal, while numeric values never equal their text rendering.
    left_type = "typeof(pontis_overlap_left.v)"
    right_type = "typeof(pontis_overlap_right.v)"
    compatible_type = (
        f"({left_type} = {right_type} OR "
        f"({left_type} IN ('integer', 'real') AND {right_type} IN ('integer', 'real')))"
    )
    return f"{equality} AND {compatible_type}"


def _row_value(row, index: int):
    if isinstance(row, dict):
        return list(row.values())[index]
    return row[index]


def _needs_intersection_sample(card_1: int, card_2: int, card_overlap: int) -> bool:
    if card_overlap <= 0:
        return False
    coverage_1 = card_overlap / card_1 if card_1 else 0.0
    coverage_2 = card_overlap / card_2 if card_2 else 0.0
    return max(coverage_1, coverage_2) < SHORT_CODE_MAX_COVERAGE or (card_1 <= 2 and card_2 <= 2)


def _is_disabled_overlap_stats(
    col1: Dict,
    col2: Dict,
    card_1: int,
    card_2: int,
    card_overlap: int,
    intersection_sample: list[str],
    *,
    allow_numeric_temporal: bool = False,
) -> bool:
    if _below_overlap_threshold_counts(card_1, card_2, card_overlap):
        return True
    if card_1 <= 1 or card_2 <= 1:
        return True
    if _is_boolean_type(col1.get("data_type")) and _is_boolean_type(col2.get("data_type")):
        return True
    if card_1 <= 2 and card_2 <= 2 and intersection_sample and _is_boolean_domain_sample(intersection_sample):
        return True
    if _is_short_code_collision_stats(card_1, card_2, card_overlap, intersection_sample):
        return True
    if not allow_numeric_temporal:
        if _is_numeric_type(col1.get("data_type")) and _is_numeric_type(col2.get("data_type")):
            return True
        if _is_temporal_type(col1.get("data_type")) and _is_temporal_type(col2.get("data_type")):
            return True
    return False


def _pair_overlap_payload(col1: Dict, col2: Dict, overlap_result: Dict) -> Dict:
    stats = {
        key: value
        for key, value in overlap_result.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    stats["overlap_coefficient"] = overlap_result["overlap_coefficient"]
    payload = {
        "from_table": col1["table"],
        "from_table_name": col1.get("table_name") or col1["table"],
        "from_column": col1["column"],
        "from_ref": col1["entity_name"],
        "from_type": col1["data_type"],
        "to_table": col2["table"],
        "to_table_name": col2.get("table_name") or col2["table"],
        "to_column": col2["column"],
        "to_ref": col2["entity_name"],
        "to_type": col2["data_type"],
        "sources": ["value_domain"],
        "stats": stats,
    }
    if _domain_members(col1) or _domain_members(col2):
        payload["domain_sides"] = [_domain_side_payload(col1), _domain_side_payload(col2)]
    return payload


def _domain_members(col: Dict) -> list[Dict]:
    members = col.get("domain_members")
    return members if isinstance(members, list) else []


def _column_payload(col: Dict) -> Dict:
    return {
        "ref": col["entity_name"],
        "table": col["table"],
        "table_name": col.get("table_name") or col["table"],
        "table_ref": col.get("table_ref") or col["table"],
        "column": col["column"],
        "type": col.get("data_type", ""),
    }


def _domain_side_payload(col: Dict) -> Dict:
    members = _domain_members(col) or [col]
    return {
        "domain_ref": col.get("entity_name"),
        "domain_unit": col.get("domain_unit") or col.get("table"),
        "domain_role": col.get("domain_role") or col.get("column"),
        "domain_member_count": len(members),
        "members": [_column_payload(member) for member in members],
    }


def _split_domain_pair_overlaps(pair_overlaps: list[Dict]) -> tuple[list[Dict], list[Dict]]:
    domain_overlaps: list[Dict] = []
    physical_overlaps: list[Dict] = []
    for overlap in pair_overlaps:
        if overlap.get("domain_sides"):
            domain_overlaps.append(_domain_pair_overlap_to_group(overlap))
        else:
            physical_overlaps.append(overlap)
    return domain_overlaps, physical_overlaps


def _domain_pair_overlap_to_group(overlap: Dict) -> Dict:
    columns_by_ref: dict[str, Dict] = {}
    for side in overlap.get("domain_sides") or []:
        for column in side.get("members") or []:
            columns_by_ref[column["ref"]] = column
    columns = sorted(columns_by_ref.values(), key=lambda item: (item["table"], item["column"], item["ref"]))
    stats = dict(overlap.get("stats") or {})
    stats.update({
        "column_count": len(columns),
        "domain_count": len(overlap.get("domain_sides") or []),
        "pair_count": 1,
        "min_overlap_coefficient": stats.get("overlap_coefficient", 0),
        "max_overlap_coefficient": stats.get("overlap_coefficient", 0),
    })
    return {
        "columns": columns,
        "sources": overlap.get("sources", ["value_domain"]),
        "filter_evidence": overlap.get("filter_evidence", {}),
        "filter_pipeline": overlap.get("filter_pipeline", []),
        "domain_sides": overlap.get("domain_sides") or [],
        "pair_stats": [{
            "from_ref": overlap.get("from_ref"),
            "to_ref": overlap.get("to_ref"),
            "from_domain_role": (overlap.get("domain_sides") or [{}])[0].get("domain_role"),
            "to_domain_role": (overlap.get("domain_sides") or [{}, {}])[1].get("domain_role"),
            "stats": overlap.get("stats") or {},
        }],
        "stats": stats,
    }


def _below_overlap_threshold_counts(card_1: int, card_2: int, card_overlap: int) -> bool:
    if card_overlap >= MIN_OVERLAP_VALUES:
        return False
    coverage_1 = card_overlap / card_1 if card_1 else 0.0
    coverage_2 = card_overlap / card_2 if card_2 else 0.0
    return max(coverage_1, coverage_2) < MIN_OVERLAP_COVERAGE_OVERRIDE


def _is_boolean_type(data_type: str | None) -> bool:
    return "bool" in str(data_type or "").lower()


def _is_boolean_domain_sample(values: list[str]) -> bool:
    normalized = {value for value in values if value}
    return 0 < len(normalized) <= 2 and normalized.issubset(BOOLEAN_VALUES)


def _is_short_code_collision_stats(
    card_1: int,
    card_2: int,
    card_overlap: int,
    intersection_sample: list[str],
) -> bool:
    normalized = [value for value in intersection_sample if value]
    if not normalized:
        return False

    short_code_count = sum(1 for value in normalized if _is_short_code_value(value))
    short_code_ratio = short_code_count / len(normalized)
    if short_code_ratio < SHORT_CODE_RATIO_THRESHOLD:
        return False

    coverage_1 = card_overlap / card_1 if card_1 else 0.0
    coverage_2 = card_overlap / card_2 if card_2 else 0.0
    return max(coverage_1, coverage_2) < SHORT_CODE_MAX_COVERAGE


def _is_short_code_value(value: str) -> bool:
    return (
        len(value) <= SHORT_CODE_MAX_LENGTH
        and bool(re.fullmatch(r"[a-z0-9]+", value))
        and any(char.isdigit() for char in value)
    )


def _is_numeric_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(token in lowered for token in NUMERIC_TYPES)


def _is_temporal_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(token in lowered for token in TEMPORAL_TYPES)


def _normalize_value(value) -> str:
    return str(value).strip().lower()
