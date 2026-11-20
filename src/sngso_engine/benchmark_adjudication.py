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
    outcome = payload["outcome"]
    upheld_ids = payload.get("upheld_review_ids", [])
    upheld_valid = (
        bool(upheld_ids)
        and len(upheld_ids) == len(set(upheld_ids))
        and set(upheld_ids).issubset(set(payload["review_ids"]))
        if outcome == "uphold_existing"
        else not upheld_ids
    )
    return {
        "valid": independent and upheld_valid,
        "eligible_for_adjudication": independent and upheld_valid,
        "checks": [
            {"name": "distinct_review_records", "passed": len(set(payload["review_ids"])) >= 2},
            {"name": "adjudicator_not_a_reviewer", "passed": independent},
            {"name": "upheld_review_selection", "passed": upheld_valid},
            {"name": "evidence_and_rationale_present", "passed": bool(payload["evidence_references"]) and len(payload["rationale"].strip()) >= 30},
        ],
        "scope_note": (
            "Identity, role assignment, and independence are declarations until "
            "a trusted authentication and authorization provider is integrated."
        ),
    }
