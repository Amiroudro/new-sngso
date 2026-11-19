from datetime import datetime, timezone

import pytest

from sngso_engine.permissions import OPERATIONS, evaluate_permission


NOW = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)


def permission_record() -> dict:
    return {
        "id": "permission:publisher-a",
        "status": "approved",
        "effective_from": "2026-11-18T00:00:00Z",
        "effective_until": "2026-11-20T00:00:00Z",
        "operations": {
            operation: {
                "allowed": operation == "discovery",
                "status": "approved" if operation == "discovery" else "denied",
            }
            for operation in OPERATIONS
        },
    }


def test_explicitly_approved_operation_is_allowed() -> None:
    result = evaluate_permission(permission_record(), "discovery", now=NOW)
    assert result.allowed is True
    assert result.reason == "approved"
    assert result.permission_id == "permission:publisher-a"


@pytest.mark.parametrize(
    ("record", "operation", "reason"),
    [
        (None, "discovery", "no_permission_record"),
        (permission_record(), "full_text_storage", "operation_not_approved"),
        (permission_record(), "unknown", "unknown_operation"),
    ],
)
def test_missing_or_unapproved_permission_denies(record, operation, reason) -> None:
    result = evaluate_permission(record, operation, now=NOW)
    assert result.allowed is False
    assert result.reason == reason


@pytest.mark.parametrize("malformed", [[], "not-a-record", 42])
def test_malformed_permission_record_denies(malformed) -> None:
    result = evaluate_permission(malformed, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (False, "invalid_permission_record")


@pytest.mark.parametrize("invalid_id", [None, "", "   ", [], 7])
def test_missing_or_malformed_permission_id_denies(invalid_id) -> None:
    record = permission_record()
    record["id"] = invalid_id
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (False, "invalid_permission_id")
    assert result.permission_id is None


def test_unhashable_operation_input_denies_without_exception() -> None:
    result = evaluate_permission(permission_record(), ["discovery"], now=NOW)
    assert (result.allowed, result.reason) == (False, "unknown_operation")


def test_malformed_evaluation_time_denies_without_exception() -> None:
    result = evaluate_permission(permission_record(), "discovery", now="not-a-datetime")
    assert (result.allowed, result.reason) == (False, "invalid_evaluation_time")


def test_future_permission_denies() -> None:
    record = permission_record()
    record["effective_from"] = "2026-11-20T00:00:00Z"
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (False, "permission_not_yet_effective")


def test_expired_permission_denies_at_exact_end_instant() -> None:
    record = permission_record()
    record["effective_until"] = "2026-11-19T00:00:00Z"
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (False, "permission_expired")


def test_naive_evaluation_time_denies() -> None:
    result = evaluate_permission(
        permission_record(), "discovery", now=datetime(2026, 11, 19)
    )
    assert (result.allowed, result.reason) == (
        False,
        "evaluation_time_must_be_timezone_aware",
    )


def test_naive_permission_timestamp_denies() -> None:
    record = permission_record()
    record["effective_from"] = "2026-11-18T00:00:00"
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (
        False,
        "invalid_or_missing_effective_from",
    )


def test_approved_global_status_does_not_override_denied_operation() -> None:
    record = permission_record()
    record["operations"]["discovery"] = {"allowed": True, "status": "revoked"}
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (False, "operation_not_approved")


def test_unhandled_operation_conditions_require_review() -> None:
    record = permission_record()
    record["operations"]["discovery"]["conditions"] = [
        "Only after confirming the publisher's written scope"
    ]
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (
        False,
        "operation_conditions_require_review",
    )


def test_malformed_operation_conditions_deny() -> None:
    record = permission_record()
    record["operations"]["discovery"]["conditions"] = "not-a-condition-list"
    result = evaluate_permission(record, "discovery", now=NOW)
    assert (result.allowed, result.reason) == (
        False,
        "invalid_operation_conditions",
    )
