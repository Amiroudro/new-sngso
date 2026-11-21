"""Validation for independent editorial benchmark adjudication records.

Adjudication must resolve each declared material disagreement against the exact
review set. This validates completeness and traceability, not editorial truth.
"""
from __future__ import annotations

from typing import Any

from .contracts import validate_payload


def validate_benchmark_adjudication(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate adjudication completeness and return named quality checks."""
    validate_payload("editorial-benchmark-adjudication", payload)
    reviewer_ids = payload.get("reviewer_ids", [])
    review_ids = payload.get("review_ids", [])
    adjudicator_id = payload["adjudicator"]["principal_id"]
    independent = adjudicator_id not in set(reviewer_ids)
    outcome = payload["outcome"]
    upheld_ids = payload.get("upheld_review_ids", [])
    upheld_valid = (
        bool(upheld_ids)
        and len(upheld_ids) == len(set(upheld_ids))
        and set(upheld_ids).issubset(set(review_ids))
        if outcome == "uphold_existing"
        else not upheld_ids
    )
    issues = payload.get("issue_resolutions", [])
    issue_ids = [item.get("issue_id") for item in issues]
    unique_issues = len(issue_ids) == len(set(issue_ids))
    exact_positions = all(set(item.get("reviewer_positions", {})) == set(review_ids) for item in issues)
    complete_resolutions = all(bool(item.get("resolution", "").strip()) and bool(item.get("evidence_references")) for item in issues)
    checks = [
        {"name": "distinct_review_records", "passed": len(set(review_ids)) >= 2},
        {"name": "adjudicator_not_a_reviewer", "passed": independent},
        {"name": "upheld_review_selection", "passed": upheld_valid},
        {"name": "unique_disagreement_ids", "passed": unique_issues},
        {"name": "every_review_position_recorded", "passed": exact_positions},
        {"name": "each_disagreement_resolved_with_evidence", "passed": complete_resolutions},
        {"name": "evidence_and_rationale_present", "passed": bool(payload["evidence_references"]) and len(payload["rationale"].strip()) >= 30},
    ]
    quality_checks = [check for check in checks if check["name"] != "adjudicator_not_a_reviewer"]
    valid = all(check["passed"] for check in quality_checks)
    eligible = valid and independent
    return {
        "valid": valid,
        "eligible_for_adjudication": eligible,
        "checks": checks,
        "issue_count": len(issues),
        "scope_note": (
            "The record proves that declared disagreement points were addressed, not that the issue list is exhaustive or that conclusions are correct. Identity, role assignment, and independence still depend on trusted deployment controls."
        ),
    }
