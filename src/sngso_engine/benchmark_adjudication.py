"""Validation for independent editorial benchmark adjudication records.

This validates declarations and workflow completeness. It does not authenticate
the adjudicator or prove that the adjudicator is independent.
"""
from __future__ import annotations

from typing import Any

from .contracts import validate_payload


def validate_benchmark_adjudication(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an adjudication and return a transparent eligibility report."""
    validate_payload("editorial-benchmark-adjudication", payload)
    reviewer_ids = payload.get("reviewer_ids", [])
    adjudicator_id = payload["adjudicator"]["principal_id"]
    independent = adjudicator_id not in set(reviewer_ids)
    return {
        "valid": True,
        "eligible_for_adjudication": independent,
        "checks": [
            {"name": "distinct_review_records", "passed": len(set(payload["review_ids"])) >= 2},
            {"name": "adjudicator_not_a_reviewer", "passed": independent},
            {"name": "evidence_and_rationale_present", "passed": bool(payload["evidence_references"]) and len(payload["rationale"].strip()) >= 30},
        ],
        "scope_note": (
            "Identity, role assignment, and independence are declarations until "
            "a trusted authentication and authorization provider is integrated."
        ),
    }
