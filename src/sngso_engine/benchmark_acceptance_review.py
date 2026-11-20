"""Independent, non-production benchmark acceptance review contract."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from .canonical import sha256_fingerprint
from .contracts import validate_payload

class BenchmarkAcceptanceReviewError(ValueError):
    """Raised when an acceptance review is inconsistent or overclaims scope."""

def validate_benchmark_acceptance_review(payload: dict[str, Any], *, protocol: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    """Validate a review against exact protocol/execution evidence.

    This is a contract validator, not an authorization or production approval mechanism.
    """
    validate_payload("editorial-benchmark-acceptance-review", payload)
    expected = {
        "study_id": protocol["study_id"],
        "study_version": protocol["study_version"],
        "protocol_fingerprint": sha256_fingerprint(protocol),
        "execution_id": execution["execution_id"],
        "execution_fingerprint": sha256_fingerprint(execution),
        "benchmark_id": protocol["benchmark_id"],
        "benchmark_fingerprint": protocol["benchmark_fingerprint"],
        "rubric_version": protocol["rubric_version"],
    }
    for field, value in expected.items():
        if payload[field] != value:
            raise BenchmarkAcceptanceReviewError(f"{field} does not match the supplied immutable study evidence")
    try:
        reviewed_at = datetime.fromisoformat(payload["reviewed_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BenchmarkAcceptanceReviewError("reviewed_at must be timezone-aware") from exc
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        raise BenchmarkAcceptanceReviewError("reviewed_at must be timezone-aware")
    planned_reviewers = {row["reviewer_id"] for row in protocol["reviewer_plan"]}
    methodologist = execution["analysis"]["methodologist_id"]
    if payload["reviewer_id"] in planned_reviewers or payload["reviewer_id"] == methodologist:
        raise BenchmarkAcceptanceReviewError("acceptance reviewer must be independent of blind evaluators and methodologist")
    conflict = payload["conflict_declaration"]
    if not conflict["no_conflict"] or conflict["conflicts"]:
        raise BenchmarkAcceptanceReviewError("a conflicted reviewer cannot issue an acceptance decision")
    statuses = [domain["status"] for domain in payload["domains"].values()]
    decision = payload["decision"]
    if decision in {"accepted_for_limited_use", "accepted_for_proposed_use"} and not all(s == "pass" for s in statuses):
        raise BenchmarkAcceptanceReviewError("acceptance requires statistical, editorial, and operational domains to all pass")
    if decision == "rejected" and "fail" not in statuses:
        raise BenchmarkAcceptanceReviewError("rejection requires at least one failed review domain")
    if decision == "needs_revision" and "needs_revision" not in statuses:
        raise BenchmarkAcceptanceReviewError("needs_revision requires at least one domain marked needs_revision")
    return {
        "valid": True, "decision": decision, "review_id": payload["review_id"],
        "protocol_fingerprint": expected["protocol_fingerprint"],
        "execution_fingerprint": expected["execution_fingerprint"],
        "reviewer_independent": True,
        "domains_passed": sum(s == "pass" for s in statuses), "domain_count": 3,
        "production_ready": False, "authorization_granted": False,
        "interpretation": "This is a scoped independent review record. It does not grant production authorization, independently reproduce statistical analysis, authenticate the reviewer, or override the operational security acceptance gate."
    }
