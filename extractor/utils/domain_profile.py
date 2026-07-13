"""Conservative value-domain profiling and compatibility checks.

The profile is deliberately representation-oriented.  It can prove a small
set of domains incompatible, but never claims that two columns have the same
business meaning.  Missing or mixed evidence remains compatible/unknown.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


PROFILE_VERSION = 1
# A dominant format is useful evidence for an agent, but not a hard rejection
# criterion.  A domain filter may reject only when every non-empty value has a
# representation that is provably disjoint from the other column.
DOMINANT_RATIO = 0.98
NUMERIC_TYPES = {"INT", "INTEGER", "SMALLINT", "BIGINT", "NUMBER", "NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE"}
TEXT_TYPES = {"TEXT", "VARCHAR", "CHAR", "CHARACTER", "STRING"}
TEMPORAL_TYPES = {"DATE", "TIME", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}

_STRICT_FORMATS = {"uuid", "email", "url", "ipv4", "hex", "alpha_code"}


def physical_family(data_type: str | None) -> str:
    data_type = str(data_type or "").upper().split("(", 1)[0].strip()
    if data_type in NUMERIC_TYPES:
        return "numeric"
    if data_type in TEXT_TYPES:
        return "text"
    if data_type in TEMPORAL_TYPES:
        return "temporal"
    if "BOOL" in data_type:
        return "boolean"
    if data_type == "BINARY" or data_type == "BLOB":
        return "binary"
    if data_type in {"VARIANT", "OBJECT", "ARRAY", "JSON"}:
        return "semi_structured"
    if data_type in {"GEOMETRY", "GEOGRAPHY"}:
        return "geo"
    return "other"


def build_domain_profile(
    data_type: str | None,
    *,
    nonempty_count: int = 0,
    min_value: Any = None,
    max_value: Any = None,
    min_length: int | None = None,
    max_length: int | None = None,
    integer_count: int = 0,
    fractional_count: int = 0,
    uuid_count: int = 0,
    email_count: int = 0,
    url_count: int = 0,
    ipv4_count: int = 0,
    hex_count: int = 0,
    digits_count: int = 0,
    alpha_count: int = 0,
    alnum_count: int = 0,
) -> dict[str, Any]:
    """Build one profile from full-column aggregate counters."""

    total = max(0, int(nonempty_count or 0))
    family = physical_family(data_type)

    def ratio(value: int) -> float:
        return round(max(0, int(value or 0)) / total, 6) if total else 0.0

    ratios = {
        "integer": ratio(integer_count),
        "fractional": ratio(fractional_count),
        "uuid": ratio(uuid_count),
        "email": ratio(email_count),
        "url": ratio(url_count),
        "ipv4": ratio(ipv4_count),
        "hex": ratio(hex_count),
        "digits": ratio(digits_count),
        "alpha": ratio(alpha_count),
        "alnum": ratio(alnum_count),
    }
    format_counts: dict[str, int] = {
        "uuid": int(uuid_count or 0),
        "email": int(email_count or 0),
        "url": int(url_count or 0),
        "ipv4": int(ipv4_count or 0),
        "hex": int(hex_count or 0),
    }
    if family == "numeric":
        format_counts["integral_numeric"] = int(integer_count or 0)
        format_counts["fractional_numeric"] = int(fractional_count or 0)
    else:
        fixed_length = min_length is not None and min_length == max_length
        format_counts[f"digits:{min_length}" if fixed_length else "digits"] = int(digits_count or 0)
        if (max_length or 0) <= 32:
            format_counts["alpha_code"] = int(alpha_count or 0)
        if (max_length or 0) <= 64:
            format_counts["alnum_code"] = int(alnum_count or 0)

    value_format = "unknown"
    format_count = 0
    # The order gives more specific representations priority.  Counts are
    # still preserved so callers can distinguish complete from merely dominant
    # coverage.
    for name in (
        "uuid", "email", "url", "ipv4", "hex", "integral_numeric",
        "fractional_numeric", f"digits:{min_length}", "digits", "alpha_code", "alnum_code",
    ):
        count = format_counts.get(name, 0)
        if total and count / total >= DOMINANT_RATIO:
            value_format = name
            format_count = count
            break
    confidence = ratio(format_count)
    format_is_exhaustive = bool(total and format_count == total)

    integer_bits = None
    integer_has_negative = None
    if value_format == "integral_numeric" and format_is_exhaustive:
        integer_bits, integer_has_negative = _integer_domain_bits(min_value, max_value)
    numeric_min = _decimal_text(min_value) if family == "numeric" else None
    numeric_max = _decimal_text(max_value) if family == "numeric" else None

    return {
        "version": PROFILE_VERSION,
        "physical_family": family,
        "value_format": value_format,
        "format_confidence": round(confidence, 6),
        "format_match_count": format_count,
        "format_is_exhaustive": format_is_exhaustive,
        # Observed range, not the database storage width.  A 32-bit-looking
        # foreign key can legitimately join a 64-bit-looking primary key, so
        # this feature is never a hard incompatibility rule.
        "integer_bits": integer_bits,
        "integer_has_negative": integer_has_negative,
        "numeric_min": numeric_min,
        "numeric_max": numeric_max,
        "nonempty_count": total,
        "min_length": min_length,
        "max_length": max_length,
        "ratios": ratios,
    }


def parse_domain_profile(column: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = column.get("domain_profile")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


def merge_domain_profiles(members: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Merge a logical column domain without manufacturing certainty."""

    total_members = len(members)
    profiles = [profile for member in members if (profile := parse_domain_profile(member))]
    if not profiles:
        return None
    family_counts = Counter(str(profile.get("physical_family") or "other") for profile in profiles)
    format_counts = Counter(
        str(profile.get("value_format") or "unknown")
        for profile in profiles
        if bool(profile.get("format_is_exhaustive"))
    )
    family, family_count = family_counts.most_common(1)[0]
    total = len(profiles)
    complete = total == total_members
    if family_count != total or not complete:
        family = "mixed"
    value_format = "unknown"
    confidence = 0.0
    if format_counts:
        candidate, count = format_counts.most_common(1)[0]
        if complete and count == total:
            value_format = candidate
            confidence = min(float(profile.get("format_confidence") or 0.0) for profile in profiles)
    min_lengths = [profile.get("min_length") for profile in profiles if profile.get("min_length") is not None]
    max_lengths = [profile.get("max_length") for profile in profiles if profile.get("max_length") is not None]
    integer_bits = [
        int(profile["integer_bits"])
        for profile in profiles
        if profile.get("integer_bits") is not None
    ]
    numeric_mins = [
        value
        for profile in profiles
        if (value := _decimal_value(profile.get("numeric_min"))) is not None
    ]
    numeric_maxs = [
        value
        for profile in profiles
        if (value := _decimal_value(profile.get("numeric_max"))) is not None
    ]
    return {
        "version": PROFILE_VERSION,
        "physical_family": family,
        "value_format": value_format,
        "format_confidence": round(confidence, 6),
        "format_match_count": 0,
        "format_is_exhaustive": bool(value_format != "unknown" and complete),
        "integer_bits": max(integer_bits) if value_format == "integral_numeric" and complete and integer_bits else None,
        "integer_has_negative": (
            any(bool(profile.get("integer_has_negative")) for profile in profiles)
            if value_format == "integral_numeric" and complete and integer_bits
            else None
        ),
        "numeric_min": _decimal_text(min(numeric_mins)) if complete and len(numeric_mins) == total else None,
        "numeric_max": _decimal_text(max(numeric_maxs)) if complete and len(numeric_maxs) == total else None,
        "nonempty_count": sum(int(profile.get("nonempty_count") or 0) for profile in profiles),
        "min_length": min(min_lengths) if min_lengths else None,
        "max_length": max(max_lengths) if max_lengths else None,
        "ratios": {},
        "member_profile_count": total,
        "member_profile_complete": complete,
    }


def domain_compatibility(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Return a conservative compatibility decision and auditable evidence."""

    left_profile = parse_domain_profile(left)
    right_profile = parse_domain_profile(right)
    evidence = {"left": left_profile, "right": right_profile}
    if not left_profile or not right_profile:
        return True, "profile_missing", evidence

    if _profile_is_empty(left_profile) or _profile_is_empty(right_profile):
        return False, "empty_domain", evidence

    left_format = str(left_profile.get("value_format") or "unknown")
    right_format = str(right_profile.get("value_format") or "unknown")
    left_confidence = float(left_profile.get("format_confidence") or 0.0)
    right_confidence = float(right_profile.get("format_confidence") or 0.0)

    left_exhaustive = bool(left_profile.get("format_is_exhaustive"))
    right_exhaustive = bool(right_profile.get("format_is_exhaustive"))
    if left_exhaustive and right_exhaustive:
        if _incompatible_formats(left_format, right_format):
            return False, "format_incompatible", evidence

    left_family = str(left_profile.get("physical_family") or "other")
    right_family = str(right_profile.get("physical_family") or "other")
    if left_family == right_family == "numeric" and _numeric_ranges_disjoint(left_profile, right_profile):
        return False, "numeric_ranges_disjoint", evidence
    if left_exhaustive and right_exhaustive and left_family == right_family == "numeric":
        formats = {left_format, right_format}
        if formats == {"integral_numeric", "fractional_numeric"}:
            return False, "numeric_representation_incompatible", evidence

    return True, "compatible_or_unknown", evidence


def _incompatible_formats(left: str, right: str) -> bool:
    if left == right:
        return False
    if left.startswith("digits:") and right.startswith("digits:"):
        return left != right
    if left in _STRICT_FORMATS and right in _STRICT_FORMATS:
        return _strict_formats_disjoint(left, right)
    if left.startswith("digits") and right in {"uuid", "email", "url", "ipv4", "hex", "alpha_code"}:
        return True
    if right.startswith("digits") and left in {"uuid", "email", "url", "ipv4", "hex", "alpha_code"}:
        return True
    return False


def _strict_formats_disjoint(left: str, right: str) -> bool:
    """Only encode lexical contradictions, never semantic assumptions."""

    if left == right:
        return False
    # Hex strings may be alphabetic (for example ``deadbeef``), so they are
    # intentionally not considered disjoint from alpha codes.  Likewise an
    # alphanumeric code can contain a UUID-like value and is never strict.
    if {left, right} == {"hex", "alpha_code"}:
        return False
    return True


def _integer_domain_bits(min_value: Any, max_value: Any) -> tuple[int | None, bool | None]:
    """Describe the observed integer range without treating it as a type gate."""

    try:
        low = int(min_value)
        high = int(max_value)
    except (TypeError, ValueError, OverflowError):
        return None, None
    magnitude = max(abs(low), abs(high))
    return max(1, magnitude.bit_length()), low < 0


def _profile_is_empty(profile: Mapping[str, Any]) -> bool:
    return (
        "nonempty_count" in profile
        and bool(profile.get("member_profile_complete", True))
        and int(profile.get("nonempty_count") or 0) == 0
    )


def _numeric_ranges_disjoint(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_min = _decimal_value(left.get("numeric_min"))
    left_max = _decimal_value(left.get("numeric_max"))
    right_min = _decimal_value(right.get("numeric_min"))
    right_max = _decimal_value(right.get("numeric_max"))
    if None in (left_min, left_max, right_min, right_max):
        return False
    return bool(left_max < right_min or right_max < left_min)


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value: Any) -> str | None:
    decimal_value = value if isinstance(value, Decimal) else _decimal_value(value)
    if decimal_value is None:
        return None
    return format(decimal_value, "f")
