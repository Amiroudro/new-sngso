import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sngso_engine.benchmark_assignments import transition_benchmark_assignment
from sngso_engine.contracts import validate_payload


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "editorial-benchmark-assignment.schema.json"


def _payload():
    return {
        "schema_version": "1.0.0",
        "assignment_id": "assignment:sample-001",
        "artifact_id": "benchmark:bn-001",
        "artifact_fingerprint": "sha256:" + "a" * 64,
        "language": "bn",
        "reviewer_id": "reviewer:bn-01",
        "assigned_by": "curator:01",
        "assigned_at": "2026-11-20T00:00:00Z",
        "due_at": "2026-11-21T00:00:00Z",
        "status": "assigned",
        "reason": "Independent Bangla editorial review",
    }


def test_assignment_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_assigned_and_accepted_states_pass_runtime_contract():
    payload = _payload()
    validate_payload("editorial-benchmark-assignment", payload)
    accepted = transition_benchmark_assignment(
        payload, event="accept", actor="reviewer:bn-01",
        at="2026-11-20T01:00:00Z", reason="Accepted assignment",
    )
    validate_payload("editorial-benchmark-assignment", accepted)


def test_submitted_state_passes_runtime_contract():
    payload = _payload()
    accepted = transition_benchmark_assignment(
        payload, event="accept", actor="reviewer:bn-01",
        at="2026-11-20T01:00:00Z", reason="Accepted assignment",
    )
    submitted = transition_benchmark_assignment(
        accepted, event="submit", actor="reviewer:bn-01",
        at="2026-11-20T02:00:00Z", reason="Review submitted",
        submitted_review_id="review:bn-001",
    )
    validate_payload("editorial-benchmark-assignment", submitted)


def test_cancelled_state_passes_runtime_contract():
    payload = _payload()
    cancelled = transition_benchmark_assignment(
        payload, event="cancel", actor="curator:01",
        at="2026-11-20T01:00:00Z", reason="Reviewer unavailable",
    )
    validate_payload("editorial-benchmark-assignment", cancelled)


def test_schema_rejects_missing_submission_receipt():
    payload = _payload()
    payload["status"] = "submitted"
    with pytest.raises(ValueError):
        validate_payload("editorial-benchmark-assignment", payload)
