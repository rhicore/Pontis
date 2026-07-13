"""Deterministic semantic value-domain classification for database columns.

The classifier deliberately produces multiple labels.  A column can be an
identifier, a geographic code, and text-shaped at the same time.  These labels
are suitable for blocking and ranking overlap candidates; they are not proof
that two columns are disjoint.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from extractor.utils.domain_profile import parse_domain_profile, physical_family


CLASSIFIER_VERSION = 2

_TOKEN_RE = re.compile(r"[A-Za-z]+|[0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_IDENTIFIER_TOKENS = {"id", "identifier", "uuid", "guid", "barcode", "key"}
_CODE_TOKENS = {"code", "cd", "abbr", "abbreviation"}
_HASH_TOKENS = {"hash", "checksum", "digest", "md5", "sha", "sha1", "sha256"}
_CATEGORY_TOKENS = {
    "category", "class", "classification", "kind", "status", "state", "type",
    "flag", "indicator", "level", "grade", "gender", "sex", "race", "ethnicity",
    "color", "style", "surface", "market", "zone", "hand",
}
_TEMPORAL_TOKENS = {
    "date", "datetime", "time", "timestamp", "year", "yr", "month", "quarter",
    "week", "day", "hour", "minute", "second", "created", "updated", "modified",
    "start", "end", "effective", "expiration", "expiry",
}
_GEO_TOKENS = {
    "geo", "geography", "latitude", "lat", "longitude", "lon", "lng", "zip",
    "zipcode", "postal", "fips", "country", "state", "province", "county", "city",
    "district", "region", "territory", "address", "location", "tract", "zcta",
}
_PERCENT_TOKENS = {"percent", "percentage", "pct", "ratio", "rate", "share", "proportion", "fraction"}
_CURRENCY_TOKENS = {
    "amount", "price", "cost", "revenue", "income", "wage", "salary", "rent", "fee",
    "tax", "balance", "budget", "spend", "expense", "payment", "sales", "dollar", "usd",
    "freight", "subtotal", "quota", "due", "amt",
}
_COUNT_TOKENS = {
    "count", "cnt", "total", "number", "num", "quantity", "qty", "population", "pop",
    "units", "records", "visits", "events", "occurrences", "attendance", "capacity", "runs",
    "hits", "errors", "balls", "strikes", "outs",
}
_DURATION_TOKENS = {
    "duration", "elapsed", "latency", "age", "seconds", "minutes", "hours", "days",
    "months", "years", "mins", "secs",
}
_STATISTIC_TOKENS = {
    "estimate", "estimated", "median", "average", "avg", "mean", "std", "stdev",
    "variance", "quartile", "percentile", "score", "rating", "rank", "index", "margin", "error",
    "lower", "upper", "minimum", "maximum", "min", "max",
}
_MEASUREMENT_TOKENS = {
    "distance", "length", "height", "width", "weight", "mass", "area", "volume",
    "temperature", "speed", "size", "coverage", "depth", "frequency", "value",
}
_TEXT_PAYLOAD_TOKENS = {
    "description", "comment", "comments", "note", "notes", "message", "body", "content",
    "text", "summary", "title", "label", "caption", "query", "sql",
}
_NAME_TOKENS = {"name", "firstname", "lastname", "fullname"}
_FILE_TOKENS = {"file", "filename", "filepath", "path", "directory", "folder", "uri", "url"}
_BOOLEAN_NAME_TOKENS = {"is", "has", "had", "was", "were", "can", "enabled", "disabled", "active", "valid"}

_COMPOUND_SUFFIXES = {
    "identifier": "identifier", "uuid": "uuid", "guid": "guid", "barcode": "barcode", "id": "id",
    "code": "code", "abbr": "abbr", "hash": "hash", "checksum": "checksum", "digest": "digest",
    "datetime": "datetime", "timestamp": "timestamp", "date": "date", "year": "year", "month": "month",
    "time": "time", "status": "status", "category": "category", "type": "type", "flag": "flag",
    "percentage": "percentage", "percent": "percent", "proportion": "proportion", "ratio": "ratio",
    "rate": "rate", "amount": "amount", "amt": "amt", "price": "price", "cost": "cost", "count": "count",
    "quantity": "quantity", "qty": "qty", "number": "number", "num": "num", "duration": "duration",
    "score": "score", "rating": "rating", "rank": "rank", "index": "index",
    "description": "description", "comment": "comment", "name": "name", "filename": "filename",
    "address": "address", "email": "email", "zip": "zip", "latitude": "latitude", "longitude": "longitude",
}
_COMPOUND_ROOTS = {
    "latitude", "longitude", "zipcode", "postal", "country", "province", "county", "city", "district",
    "region", "territory", "address", "location", "currency", "revenue", "income", "salary", "population",
    "attendance", "capacity", "freight", "subtotal", "quota", "color", "style", "surface", "market",
}

_GENERIC_ENTITY_TOKENS = (
    _IDENTIFIER_TOKENS
    | _CODE_TOKENS
    | {"column", "field", "value", "source", "target", "from", "to", "fk", "pk"}
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(?:https?://|www\.)", re.I)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$", re.I)
_DIGITS_RE = re.compile(r"^[+-]?\d+$")
_ALPHA_RE = re.compile(r"^[A-Za-z]+$")
_ALNUM_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def classify_semantic_domain(
    column_name: str,
    data_type: str | None,
    *,
    official_description: str = "",
    sample_values: Iterable[Any] | None = None,
    domain_profile: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Classify one column into auditable, non-exclusive value-domain labels."""

    name_tokens = _tokens(column_name) | _compound_name_tokens(column_name)
    description_tokens = _tokens(official_description)
    all_tokens = name_tokens | description_tokens
    family = physical_family(data_type)
    semantic_domains: set[str] = set()
    role_domains: set[str] = set()
    evidence: dict[str, list[str]] = {}

    def add(
        label: str,
        name_matched: set[str] | list[str] | tuple[str, ...],
        description_matched: set[str] | list[str] | tuple[str, ...] = (),
        *,
        description_can_set_role: bool = False,
    ) -> None:
        name_values = sorted(set(name_matched))
        description_values = sorted(set(description_matched))
        values = [f"name:{value}" for value in name_values] + [f"description:{value}" for value in description_values]
        if values:
            semantic_domains.add(label)
            evidence[label] = values[:12]
        if name_values or (description_can_set_role and description_values):
            role_domains.add(label)

    id_hits = name_tokens & _IDENTIFIER_TOKENS
    description_lower = str(official_description or "").lower()
    description_identifier = bool(
        re.search(r"\b(?:unique|primary|foreign)?\s*identifier\b|\bprimary key\b|\bforeign key\b", description_lower)
    )
    add("identifier", id_hits, ["identifier"] if description_identifier else [], description_can_set_role=True)
    add("code", name_tokens & _CODE_TOKENS, description_tokens & _CODE_TOKENS)
    add("hash", name_tokens & _HASH_TOKENS, description_tokens & _HASH_TOKENS, description_can_set_role=True)
    add("category", name_tokens & _CATEGORY_TOKENS, description_tokens & _CATEGORY_TOKENS)
    add("temporal", name_tokens & _TEMPORAL_TOKENS, description_tokens & _TEMPORAL_TOKENS)
    add("geographic", name_tokens & _GEO_TOKENS, description_tokens & _GEO_TOKENS)
    add("percentage_or_rate", name_tokens & _PERCENT_TOKENS, description_tokens & _PERCENT_TOKENS, description_can_set_role=True)
    add("currency", name_tokens & _CURRENCY_TOKENS, description_tokens & _CURRENCY_TOKENS, description_can_set_role=True)
    add("count", name_tokens & _COUNT_TOKENS, description_tokens & _COUNT_TOKENS, description_can_set_role=True)
    add("duration", name_tokens & _DURATION_TOKENS, description_tokens & _DURATION_TOKENS, description_can_set_role=True)
    add("statistic", name_tokens & _STATISTIC_TOKENS, description_tokens & _STATISTIC_TOKENS, description_can_set_role=True)
    add("measurement", name_tokens & _MEASUREMENT_TOKENS, description_tokens & _MEASUREMENT_TOKENS, description_can_set_role=True)
    add("text_payload", name_tokens & _TEXT_PAYLOAD_TOKENS, description_tokens & _TEXT_PAYLOAD_TOKENS)
    add("person_or_object_name", name_tokens & _NAME_TOKENS, description_tokens & _NAME_TOKENS)
    add("file_or_resource", name_tokens & _FILE_TOKENS, description_tokens & _FILE_TOKENS)
    add("boolean", name_tokens & _BOOLEAN_NAME_TOKENS)

    if family == "temporal":
        add("temporal", [f"type:{_base_type(data_type)}"])
    elif family == "boolean":
        add("boolean", [f"type:{_base_type(data_type)}"])
    elif family == "geo":
        add("geographic", [f"type:{_base_type(data_type)}"])
    elif family == "semi_structured":
        add("semi_structured", [f"type:{_base_type(data_type)}"])
    elif family == "binary":
        add("binary", [f"type:{_base_type(data_type)}"])

    representations = _representation_domains(sample_values, domain_profile)
    entity_tokens = _entity_tokens(name_tokens, column_name)
    role = _primary_role(role_domains, semantic_domains, family)
    join_likelihood = _join_likelihood(role, semantic_domains, name_tokens)
    confidence = _classification_confidence(role, role_domains, representations)
    blocking_keys = _blocking_keys(
        family=family,
        role=role,
        semantic_domains=semantic_domains,
        representations=representations,
        entity_tokens=entity_tokens,
    )

    return {
        "version": CLASSIFIER_VERSION,
        "physical_family": family,
        "primary_role": role,
        "join_likelihood": join_likelihood,
        "classification_confidence": confidence,
        "semantic_domains": sorted(semantic_domains) or ["unclassified"],
        "representation_domains": sorted(representations) or ["unknown"],
        "entity_tokens": entity_tokens,
        "blocking_keys": blocking_keys,
        "evidence": evidence,
    }


def _tokens(text: str) -> set[str]:
    expanded = _CAMEL_BOUNDARY_RE.sub("_", str(text or ""))
    return {token.lower() for token in _TOKEN_RE.findall(expanded)}


def _compound_name_tokens(text: str) -> set[str]:
    """Recover semantic suffixes from lowercase names such as ``customerid``."""

    compact = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    if not compact:
        return set()
    tokens: set[str] = set()
    remainder = compact
    suffixes = sorted(_COMPOUND_SUFFIXES.items(), key=lambda item: len(item[0]), reverse=True)
    while remainder:
        matched = False
        for suffix, token in suffixes:
            if remainder.endswith(suffix) and len(remainder) > len(suffix):
                tokens.add(token)
                remainder = remainder[: -len(suffix)]
                matched = True
                break
        if not matched:
            break
    if remainder and remainder != compact and len(remainder) >= 2:
        tokens.add(remainder)
    tokens.update(root for root in _COMPOUND_ROOTS if root in compact)
    return tokens


def _entity_tokens(name_tokens: set[str], column_name: str) -> list[str]:
    generic = (
        _GENERIC_ENTITY_TOKENS | _CATEGORY_TOKENS | _TEMPORAL_TOKENS | _PERCENT_TOKENS
        | _CURRENCY_TOKENS | _COUNT_TOKENS | _DURATION_TOKENS | _STATISTIC_TOKENS
        | _MEASUREMENT_TOKENS | _TEXT_PAYLOAD_TOKENS | _BOOLEAN_NAME_TOKENS
    )
    compact = re.sub(r"[^a-z0-9]", "", str(column_name or "").lower())
    candidates = {token for token in name_tokens if token not in generic and not token.isdigit()}
    if len(name_tokens) > 1:
        candidates.discard(compact)
    return sorted(candidates)[:8]


def _primary_role(role_domains: set[str], all_domains: set[str], family: str) -> str:
    if "identifier" in role_domains or "hash" in role_domains:
        return "identifier"
    if "code" in role_domains and not role_domains & {"percentage_or_rate", "currency", "count", "measurement"}:
        return "categorical_key"
    if "temporal" in role_domains:
        return "temporal_key" if family in {"numeric", "text", "temporal"} else "temporal"
    if "geographic" in role_domains and role_domains & {"code", "category"}:
        return "geographic_key"
    if role_domains & {"percentage_or_rate", "currency", "count", "duration", "statistic", "measurement"}:
        return "measure"
    if "category" in role_domains or "boolean" in role_domains:
        return "categorical"
    if "person_or_object_name" in role_domains:
        return "name"
    if "text_payload" in role_domains:
        return "text_payload"
    if "geographic" in role_domains:
        return "geographic_attribute"
    if family in {"semi_structured", "binary", "geo"}:
        return family
    # Description-only category/name/text evidence remains useful for blocking,
    # but is intentionally too weak to determine the primary role.
    return "unknown"


def _join_likelihood(role: str, domains: set[str], name_tokens: set[str]) -> str:
    if role == "identifier":
        return "high"
    if role in {"categorical_key", "geographic_key", "temporal_key"}:
        return "medium"
    if role == "name" or ("category" in domains and name_tokens & {"code", "name", "status", "type"}):
        return "medium"
    if role in {"measure", "text_payload", "geographic_attribute", "semi_structured", "binary", "geo"}:
        return "low"
    return "unknown"


def _classification_confidence(role: str, role_domains: set[str], representations: set[str]) -> str:
    if role == "unknown":
        return "low"
    if role_domains & {"identifier", "hash", "code", "temporal", "boolean"}:
        return "high"
    if representations & {"uuid", "email", "url", "ipv4", "sample:uuid", "sample:email", "sample:url", "sample:ipv4"}:
        return "high"
    return "medium"


def _representation_domains(
    sample_values: Iterable[Any] | None,
    domain_profile: Mapping[str, Any] | str | None,
) -> set[str]:
    result: set[str] = set()
    profile = parse_domain_profile({"domain_profile": domain_profile}) if domain_profile else None
    if profile:
        value_format = str(profile.get("value_format") or "unknown")
        if value_format != "unknown":
            result.add(value_format)
        min_length = profile.get("min_length")
        max_length = profile.get("max_length")
        if min_length is not None and min_length == max_length:
            result.add(f"fixed_length:{min_length}")
        bits = profile.get("integer_bits")
        if bits is not None:
            result.add(f"integer_bits:{bits}")

    values = [str(value).strip() for value in (sample_values or []) if value is not None and str(value).strip()]
    if not values:
        return result
    matchers = (
        ("uuid", _UUID_RE), ("email", _EMAIL_RE), ("url", _URL_RE),
        ("ipv4", _IPV4_RE), ("digits", _DIGITS_RE), ("alpha", _ALPHA_RE),
        ("alnum_code", _ALNUM_CODE_RE),
    )
    for label, pattern in matchers:
        if all(pattern.match(value) for value in values):
            result.add(f"sample:{label}")
            break
    lengths = {len(value) for value in values}
    if len(lengths) == 1:
        result.add(f"sample_fixed_length:{next(iter(lengths))}")
    if all(_HEX_RE.match(value) for value in values) and any(re.search(r"[a-f]", value, re.I) for value in values):
        result.add("sample:hex")
    return result


def _blocking_keys(
    *,
    family: str,
    role: str,
    semantic_domains: set[str],
    representations: set[str],
    entity_tokens: list[str],
) -> list[str]:
    keys = {f"family:{family}", f"role:{role}"}
    keys.update(f"semantic:{domain}" for domain in semantic_domains)
    keys.update(f"representation:{domain}" for domain in representations)
    keys.update(f"entity:{token}" for token in entity_tokens)
    if role in {"identifier", "categorical_key", "geographic_key", "categorical", "name"}:
        keys.add("joinable:categorical")
    if role in {"identifier", "categorical_key", "geographic_key"}:
        keys.add("joinable:key")
    if role == "temporal_key":
        keys.add("joinable:temporal")
    if role in {"geographic_key", "geographic_attribute"}:
        keys.add("joinable:geographic")
    if role == "measure":
        keys.add("non_key:measure")
    return sorted(keys)


def _base_type(data_type: str | None) -> str:
    return str(data_type or "").upper().split("(", 1)[0].strip() or "UNKNOWN"
