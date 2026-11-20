from copy import deepcopy

import pytest

from sngso_engine.benchmark_assignments import (
    BenchmarkAssignmentError,
    transition_benchmark_assignment,
    validate_benchmark_assignment,
)


NOW = "2026-11-20T12:00:00+00:00"
DUE = "2026-11-21T12:00:00+00:00"


def assignment(**overrides):
    value = {
        "schema_version": "1.0.0",
        "assignment_id": "assign-001",
        "artifact_id": "bn-benchmark-001",
        "artifact_fingerprint": "sha256:" + "a" * 64,
        "language": "bn",
        "reviewer_id": "reviewer:bn:1",
        "assigned_by": "curator:1",
        "assigned_at": NOW,
        "due_at": DUE,
        "status": "assigned",
        "reason": "Independent Bangla review",
    }
    value.update(overrides)
    return value


def test_valid_assignment_is_bound_to_exact_artifact_and_language():
    assert validate_benchmark_assignment(assignment()) == {"valid": True, "failures": []}


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewer_id": "curator:1"},
        {"language": "fr"},
        {"artifact_fingerprint": "sha256:bad"},
        {"due_at": NOW},
        {"assigned_at": "2026-11-20T12:00:00"},
    ],
)
def test_invalid_assignment_declarations_are_rejected(changes):
    assert not validate_benchmark_assignment(assignment(**changes))["valid"]


def test_assigned_reviewer_can_accept_before_deadline():
    result = transition_benchmark_assignment(
        assignment(), event="accept", actor="reviewer:bn:1",
        at=NOW, reason="Accepted for independent review",
    )
    assert result["status"] == "accepted"
    assert result["accepted_at"] == NOW


def test_only_assignee_can_accept_or_submit():
    with pytest.raises(BenchmarkAssignmentError, match="assigned reviewer"):
        transition_benchmark_assignment(
            assignment(), event="accept", actor="reviewer:other",
            at=NOW, reason="wrong actor",
        )


def test_submission_requires_acceptance_and_review_reference():
    accepted = assignment(status="accepted", accepted_at=NOW)
    with pytest.raises(BenchmarkAssignmentError, match="review ID"):
        transition_benchmark_assignment(
            accepted, event="submit", actor="reviewer:bn:1",
            at=NOW, reason="submit",
        )
    submitted = transition_benchmark_assignment(
        accepted, event="submit", actor="reviewer:bn:1",
        at=NOW, reason="review completed", submitted_review_id="review-001",
    )
    assert submitted["status"] == "submitted"
    assert submitted["submitted_review_id"] == "review-001"


def test_reviewer_cannot_accept_or_submit_at_deadline():
    with pytest.raises(BenchmarkAssignmentError, match="deadline"):
        transition_benchmark_assignment(
            assignment(), event="accept", actor="reviewer:bn:1",
            at=DUE, reason="too late",
        )


def test_expiry_requires_deadline_and_assigner():
    with pytest.raises(BenchmarkAssignmentError, match="at or after"):
        transition_benchmark_assignment(
            assignment(), event="expire", actor="curator:1",
            at=NOW, reason="too early",
        )
    expired = transition_benchmark_assignment(
        assignment(), event="expire", actor="curator:1",
        at=DUE, reason="deadline passed",
    )
    assert expired["status"] == "expired"


def test_cancel_is_limited_to_participants_and_open_states():
    with pytest.raises(BenchmarkAssignmentError, match="assignee or assigner"):
        transition_benchmark_assignment(
            assignment(), event="cancel", actor="outsider",
            at=NOW, reason="unauthorized",
        )
    cancelled = transition_benchmark_assignment(
        assignment(), event="cancel", actor="reviewer:bn:1",
        at=NOW, reason="reviewer unavailable",
    )
    assert cancelled["status"] == "cancelled"


def test_submitted_assignment_is_terminal():
    submitted = assignment(
        status="submitted", accepted_at=NOW, submitted_review_id="review-001",
        closed_at=NOW,
    )
    with pytest.raises(BenchmarkAssignmentError, match="accepted assignment"):
        transition_benchmark_assignment(
            submitted, event="submit", actor="reviewer:bn:1",
            at=NOW, reason="duplicate", submitted_review_id="review-002",
        )


def test_transition_does_not_mutate_prior_record_and_records_actor_reason():
    original = assignment()
    before = deepcopy(original)
    changed = transition_benchmark_assignment(
        original, event="accept", actor="reviewer:bn:1",
        at=NOW, reason="accepted",
    )
    assert original == before
    assert changed["last_transition"] == {
        "event": "accept", "actor": "reviewer:bn:1", "at": NOW, "reason": "accepted",
    }


def test_accepted_assignment_cannot_be_cancelled_before_acceptance():
    accepted = assignment(
        status="accepted", accepted_at="2026-11-20T13:00:00+00:00",
    )
    with pytest.raises(BenchmarkAssignmentError, match="cannot predate acceptance"):
        transition_benchmark_assignment(
            accepted, event="cancel", actor="curator:1",
            at=NOW, reason="invalid chronology",
        )
