import pytest

from sngso_engine.contracts import ContractValidationError, load_schema, validate_payload


def test_runtime_loader_loads_known_contract() -> None:
    assert load_schema("article")["title"] == "Article"


def test_runtime_loader_rejects_unknown_contract() -> None:
    with pytest.raises(FileNotFoundError):
        load_schema("not-a-real-contract")


def test_runtime_validator_accepts_valid_article() -> None:
    validate_payload(
        "article",
        {
            "schema_version": "1.0.0",
            "id": "article:runtime-001",
            "source_id": "source:publisher-a",
            "canonical_uri": "https://example.org/report",
            "title": "A report",
            "source_language": "en",
            "discovered_at": "2026-11-19T00:00:00Z",
            "record_status": "active",
        },
    )


def test_runtime_validator_rejects_missing_required_field() -> None:
    with pytest.raises(ContractValidationError) as error:
        validate_payload("article", {"schema_version": "1.0.0"})
    assert "source_id" in str(error.value)
