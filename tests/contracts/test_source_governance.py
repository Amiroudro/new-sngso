from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from sngso_engine.contracts import ContractValidationError, load_schema, validate_payload
from sngso_engine.permissions import OPERATIONS, evaluate_permission


STAMP = "2026-11-19T00:00:00Z"


def source_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "source:publisher-a",
        "publisher_name": "Publisher A",
        "publisher_type": "newspaper",
        "canonical_homepage": "https://news.example.org/",
        "domains": ["news.example.org"],
        "country_code": "BD",
        "languages": ["bn", "en"],
        "status": "active",
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def endpoint_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "endpoint:publisher-a-rss",
        "source_id": "source:publisher-a",
        "endpoint_uri": "https://news.example.org/feed.xml",
        "endpoint_type": "rss",
        "status": "active",
        "supported_languages": ["bn"],
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def evidence_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "permission-evidence:publisher-a-001",
        "source_id": "source:publisher-a",
        "evidence_type": "written_grant",
        "evidence_uri": "https://news.example.org/permissions/letter",
        "captured_at": STAMP,
        "content_fingerprint": "sha256:" + "a" * 64,
        "scope_description": "Written grant for discovery of the named RSS endpoint only.",
        "review_status": "verified",
        "reviewed_at": STAMP,
        "reviewed_by": "rights-reviewer",
    }


def permission_grant() -> dict:
    operations = {
        operation: {
            "allowed": operation == "discovery",
            "status": "approved" if operation == "discovery" else "denied",
            "conditions": [],
            **(
                {"evidence_ids": ["permission-evidence:publisher-a-001"]}
                if operation == "discovery"
                else {}
            ),
        }
        for operation in OPERATIONS
    }
    return {
        "schema_version": "1.0.0",
        "id": "permission:publisher-a-discovery-001",
        "source_id": "source:publisher-a",
        "endpoint_ids": ["endpoint:publisher-a-rss"],
        "status": "approved",
        "operations": operations,
        "evidence_ids": ["permission-evidence:publisher-a-001"],
        "basis": "Reviewed written permission with endpoint-specific scope.",
        "effective_from": "2026-11-18T00:00:00Z",
        "effective_until": "2026-12-18T00:00:00Z",
        "created_at": STAMP,
        "reviewed_at": STAMP,
        "reviewed_by": "rights-reviewer",
    }


@pytest.mark.parametrize(
    "contract",
    ["source", "source-endpoint", "permission-evidence", "source-permission-grant"],
)
def test_source_governance_schemas_are_valid(contract: str) -> None:
    Draft202012Validator.check_schema(load_schema(contract))


@pytest.mark.parametrize(
    ("contract", "record"),
    [
        ("source", source_record()),
        ("source-endpoint", endpoint_record()),
        ("permission-evidence", evidence_record()),
        ("source-permission-grant", permission_grant()),
    ],
)
def test_valid_source_governance_records(contract: str, record: dict) -> None:
    validate_payload(contract, record)


def test_grant_shape_is_compatible_with_permission_evaluator() -> None:
    result = evaluate_permission(
        permission_grant(),
        "discovery",
        source_id="source:publisher-a",
        endpoint_id="endpoint:publisher-a-rss",
    )
    assert (result.allowed, result.reason) == (True, "approved")


@pytest.mark.parametrize(
    ("source_id", "endpoint_id", "reason"),
    [
        (None, "endpoint:publisher-a-rss", "source_scope_mismatch"),
        ("source:another-publisher", "endpoint:publisher-a-rss", "source_scope_mismatch"),
        ("source:publisher-a", None, "endpoint_scope_mismatch"),
        ("source:publisher-a", "endpoint:another-feed", "endpoint_scope_mismatch"),
    ],
)
def test_grant_rejects_missing_or_mismatched_source_scope(source_id, endpoint_id, reason) -> None:
    result = evaluate_permission(
        permission_grant(),
        "discovery",
        source_id=source_id,
        endpoint_id=endpoint_id,
    )
    assert (result.allowed, result.reason) == (False, reason)


def test_grant_rejects_missing_permission_evidence() -> None:
    record = permission_grant()
    record["evidence_ids"] = []
    result = evaluate_permission(
        record,
        "discovery",
        source_id="source:publisher-a",
        endpoint_id="endpoint:publisher-a-rss",
    )
    assert (result.allowed, result.reason) == (False, "permission_evidence_missing")


def test_grant_rejects_missing_operation_evidence() -> None:
    record = permission_grant()
    record["operations"]["discovery"].pop("evidence_ids")
    result = evaluate_permission(
        record,
        "discovery",
        source_id="source:publisher-a",
        endpoint_id="endpoint:publisher-a-rss",
    )
    assert (result.allowed, result.reason) == (False, "operation_evidence_missing")


@pytest.mark.parametrize(
    ("mutate", "contract"),
    [
        (lambda value: value.pop("domains"), "source"),
        (lambda value: value.update({"domains": ["https://news.example.org"]}), "source"),
        (lambda value: value.update({"endpoint_uri": "not a uri"}), "source-endpoint"),
        (lambda value: value.pop("reviewed_by"), "permission-evidence"),
        (lambda value: value.pop("endpoint_ids"), "source-permission-grant"),
        (lambda value: value.pop("evidence_ids"), "source-permission-grant"),
        (lambda value: value.pop("effective_until"), "source-permission-grant"),
        (lambda value: value["operations"]["discovery"].pop("evidence_ids"), "source-permission-grant"),
        (lambda value: value.update({"status": "revoked"}), "source-permission-grant"),
    ],
)
def test_source_governance_contracts_reject_incomplete_records(mutate, contract: str) -> None:
    records = {
        "source": source_record(),
        "source-endpoint": endpoint_record(),
        "permission-evidence": evidence_record(),
        "source-permission-grant": permission_grant(),
    }
    record = deepcopy(records[contract])
    mutate(record)
    with pytest.raises(ContractValidationError):
        validate_payload(contract, record)


def test_permission_grant_cannot_scope_to_an_empty_endpoint_set() -> None:
    record = permission_grant()
    record["endpoint_ids"] = []
    with pytest.raises(ContractValidationError):
        validate_payload("source-permission-grant", record)


def test_permission_grant_cannot_approve_without_review_metadata() -> None:
    record = permission_grant()
    record.pop("reviewed_by")
    with pytest.raises(ContractValidationError):
        validate_payload("source-permission-grant", record)


def test_permission_grant_requires_revocation_details() -> None:
    record = permission_grant()
    record["status"] = "revoked"
    record["revoked_at"] = STAMP
    record["revocation_reason"] = "Publisher withdrew permission."
    record.pop("reviewed_at")
    record.pop("reviewed_by")
    validate_payload("source-permission-grant", record)


@pytest.mark.parametrize("missing_field", ["revoked_at", "revocation_reason"])
def test_revoked_permission_requires_audit_details(missing_field: str) -> None:
    record = permission_grant()
    record["status"] = "revoked"
    record["revoked_at"] = STAMP
    record["revocation_reason"] = "Publisher withdrew permission."
    record.pop(missing_field)
    with pytest.raises(ContractValidationError):
        validate_payload("source-permission-grant", record)
