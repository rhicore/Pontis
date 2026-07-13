from extractor.utils.semantic_domain import classify_semantic_domain


def test_identifier_with_different_business_tokens_stays_identifier():
    submitter = classify_semantic_domain("submitter_id", "TEXT")
    barcode = classify_semantic_domain("case_barcode", "TEXT", official_description="Case identifier barcode")

    assert submitter["primary_role"] == "identifier"
    assert barcode["primary_role"] == "identifier"
    assert submitter["entity_tokens"] == ["submitter"]
    assert barcode["entity_tokens"] == ["case"]


def test_statistical_float_is_measure_not_join_key():
    profile = classify_semantic_domain(
        "female_25_to_34_percent",
        "FLOAT",
        official_description="Estimated percentage of women aged 25 to 34",
    )

    assert profile["primary_role"] == "measure"
    assert profile["join_likelihood"] == "low"
    assert "percentage_or_rate" in profile["semantic_domains"]
    assert "statistic" in profile["semantic_domains"]


def test_geographic_code_keeps_multiple_domains():
    profile = classify_semantic_domain("state_fips_code", "TEXT")

    assert profile["primary_role"] == "categorical_key"
    assert {"code", "geographic"}.issubset(profile["semantic_domains"])


def test_samples_add_representation_without_becoming_hard_semantics():
    profile = classify_semantic_domain(
        "entity",
        "TEXT",
        sample_values=["a96059eb-81af-4b24-ae50-9242c0d8f819"],
    )

    assert "sample:uuid" in profile["representation_domains"]
    assert profile["primary_role"] == "unknown"


def test_lowercase_compound_names_recover_suffix_roles():
    assert classify_semantic_domain("currencyrateid", "NUMBER")["primary_role"] == "identifier"
    assert classify_semantic_domain("modifieddate", "TEXT")["primary_role"] == "temporal_key"
    assert classify_semantic_domain("endofdayrate", "FLOAT")["primary_role"] == "measure"


def test_description_year_does_not_turn_measure_into_temporal_key():
    profile = classify_semantic_domain(
        "female_population",
        "FLOAT",
        official_description="Estimated female population in survey year 2020",
    )

    assert profile["primary_role"] == "measure"
    assert "temporal" in profile["semantic_domains"]
