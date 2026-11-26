"""Cursor-bound PostgreSQL benchmark quality reports.

These reports are read-only and lock every row whose current state affects the
result. They intentionally never grant production authorization.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .benchmark_reviews import validate_human_benchmark_review
from .canonical import sha256_fingerprint
from .postgres_permission_read import evaluate_postgres_grant_for_staging


class PostgresBenchmarkQualityError(RuntimeError):
    """Raised when benchmark evidence cannot be read consistently."""


def _object(raw: Any, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PostgresBenchmarkQualityError(f"{label} is corrupt JSON") from exc
    if not isinstance(value, dict):
        raise PostgresBenchmarkQualityError(f"{label} must be a JSON object")
    return value


def _check(checks: list[dict[str, Any]], blockers: list[Any], name: str,
           passed: bool, detail: str, *, structured: bool = False) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})
    if not passed:
        blockers.append({"check": name, "detail": detail} if structured else detail)


def _review_source_permissions(cursor: Any, review: dict[str, Any],
                               now: datetime | None) -> tuple[bool, str]:
    for source in review.get("source_materials", []):
        cursor.execute(
            """SELECT payload_json FROM domain_records
               WHERE record_type = 'article' AND id = %s FOR SHARE""",
            (source["article_id"],),
        )
        article_row = cursor.fetchone()
        cursor.execute(
            "SELECT status FROM sources WHERE id = %s FOR SHARE",
            (source["source_id"],),
        )
        publisher_row = cursor.fetchone()
        cursor.execute(
            """SELECT source_id, review_status FROM permission_evidence
               WHERE id = %s FOR SHARE""",
            (source["approved_use_reference"],),
        )
        evidence_row = cursor.fetchone()
        if article_row is None or publisher_row is None or evidence_row is None:
            return False, "Benchmark source material must reference a registered article, publisher, and verified permission evidence."
        article = _object(article_row[0], "benchmark source article")
        if (publisher_row[0] != "active" or article.get("source_id") != source["source_id"]
                or evidence_row[0] != source["source_id"] or evidence_row[1] != "verified"):
            return False, "Benchmark article, publisher, and verified permission evidence must belong to the same active publisher."
        decision = evaluate_postgres_grant_for_staging(
            cursor, source["permission_grant_id"], source["permission_operation"],
            endpoint_id=source["endpoint_id"], now=now,
        )
        if not decision.allowed:
            return False, f"Benchmark permission grant is not currently usable: {decision.reason}."
        cursor.execute(
            """SELECT source_id, endpoint_uri, status FROM source_endpoints
               WHERE id = %s FOR SHARE""",
            (source["endpoint_id"],),
        )
        endpoint = cursor.fetchone()
        if endpoint is None or endpoint[0] != source["source_id"] or endpoint[2] != "active":
            return False, "Benchmark source endpoint is missing, inactive, or belongs to another publisher."
        cursor.execute(
            "SELECT id, source_id, status, effective_from, effective_until, payload_json "
            "FROM permission_grants WHERE id = %s FOR SHARE",
            (source["permission_grant_id"],),
        )
        grant_row = cursor.fetchone()
        if grant_row is None or grant_row[1] != source["source_id"]:
            return False, "Benchmark permission grant belongs to another publisher or is missing."
        grant = _object(grant_row[5], "benchmark permission grant")
        operation = grant.get("operations", {}).get(source["permission_operation"], {})
        if (grant.get("status") != "approved" or operation.get("allowed") is not True
                or operation.get("status") != "approved"
                or source["endpoint_id"] not in grant.get("endpoint_ids", [])
                or source["approved_use_reference"] not in grant.get("evidence_ids", [])
                or source["approved_use_reference"] not in operation.get("evidence_ids", [])):
            return False, "Benchmark grant does not explicitly authorize this endpoint, operation, and evidence."
        if article.get("canonical_uri") != source["canonical_uri"]:
            return False, "Benchmark source URI does not match the registered article."
        if article.get("content_fingerprint") and article["content_fingerprint"] != source["content_fingerprint"]:
            return False, "Benchmark source fingerprint does not match the registered article."
    return True, "Every source has verified evidence and a currently usable grant for its declared endpoint and operation."


def _benchmark_review_quality_report(cursor: Any, review: dict[str, Any],
                                     now: datetime | None) -> dict[str, Any]:
    metadata = validate_human_benchmark_review(review)
    artifact_ok = True
    artifact_detail = "Registered benchmark artifact fingerprint and language are verified."
    cursor.execute(
        """SELECT language, canonicalization, content_fingerprint, content_json
           FROM benchmark_artifacts WHERE artifact_id = %s FOR SHARE""",
        (review.get("benchmark_id"),),
    )
    artifact_row = cursor.fetchone()
    try:
        if artifact_row is None:
            raise ValueError("benchmark artifact is not registered")
        artifact = _object(artifact_row[3], "benchmark artifact")
        if sha256_fingerprint(artifact) != artifact_row[2]:
            raise ValueError("registered benchmark artifact fingerprint verification failed")
        if artifact_row[1] != "canonical-json-v1":
            raise ValueError("unsupported benchmark artifact canonicalization")
        if artifact_row[0] != review.get("language"):
            raise ValueError("benchmark artifact language does not match the review language")
        if artifact_row[2] != review.get("benchmark_fingerprint"):
            raise ValueError("benchmark review fingerprint does not match registered artifact content")
    except (TypeError, ValueError, PostgresBenchmarkQualityError) as exc:
        artifact_ok, artifact_detail = False, f"Benchmark artifact check failed: {exc}"

    checks: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    approved = review.get("status") == "approved"
    _check(checks, blockers, "approved_review", approved,
           "Review must be approved before it can qualify as a current benchmark reference.", structured=True)
    metadata_ok = bool(metadata["valid"] and (metadata["approval_eligible"] if approved else True))
    _check(checks, blockers, "review_metadata", metadata_ok,
           "Review metadata satisfies the human-review contract." if metadata_ok else
           "Review metadata has validation failures.", structured=True)
    _check(checks, blockers, "benchmark_artifact", artifact_ok, artifact_detail, structured=True)
    permission_ok = True
    permission_detail = "Not required for a non-approved review."
    if approved:
        try:
            permission_ok, permission_detail = _review_source_permissions(cursor, review, now)
        except (KeyError, TypeError, ValueError, PostgresBenchmarkQualityError) as exc:
            permission_ok, permission_detail = False, f"Current source permission check failed: {exc}"
    _check(checks, blockers, "current_source_permission", permission_ok, permission_detail, structured=True)

    cursor.execute(
        """SELECT adjudication_id, payload_json FROM benchmark_adjudications
           WHERE benchmark_id = %s ORDER BY resolved_at, created_at, adjudication_id
           FOR SHARE""",
        (review.get("benchmark_id"),),
    )
    adjudications = cursor.fetchall()
    adjudication_ok = len(adjudications) <= 1
    adjudication_detail = "No adjudication currently applies to this benchmark."
    adjudication_summary = None
    if len(adjudications) > 1:
        adjudication_detail = "Multiple final adjudications exist for one immutable artifact; eligibility is blocked pending data review."
        adjudication_summary = {"conflict": True, "adjudication_ids": [row[0] for row in adjudications]}
    elif adjudications:
        decision = _object(adjudications[0][1], "benchmark adjudication")
        adjudication_summary = {
            "adjudication_id": decision["adjudication_id"], "outcome": decision["outcome"],
            "resolved_at": decision["resolved_at"], "upheld_review_ids": decision.get("upheld_review_ids", []),
        }
        outcome = decision["outcome"]
        if outcome == "uphold_existing":
            adjudication_ok = review.get("review_id") in decision.get("upheld_review_ids", []) and approved
            adjudication_detail = ("This exact approved review was explicitly upheld." if adjudication_ok else
                                   "The adjudicator upheld only explicitly listed approved reviews; this review was not selected.")
        elif outcome == "return_for_revision":
            adjudication_ok, adjudication_detail = False, "The adjudication returned this immutable artifact for revision; register a new artifact and obtain fresh reviews."
        elif outcome == "reject_benchmark_item":
            adjudication_ok, adjudication_detail = False, "The adjudication rejected this benchmark item; it is not eligible for current use."
        elif outcome == "split_accepted_answers":
            adjudication_ok, adjudication_detail = False, "The adjudication requires distinct accepted-answer items; register a new artifact and obtain fresh reviews."
    _check(checks, blockers, "adjudication_policy", adjudication_ok, adjudication_detail, structured=True)
    return {
        "review_id": review.get("review_id"), "benchmark_id": review.get("benchmark_id"),
        "status": review.get("status"), "adjudication": adjudication_summary,
        "eligible_for_current_use": bool(approved and metadata["approval_eligible"] and artifact_ok and permission_ok and adjudication_ok),
        "blockers": blockers, "checks": checks,
        "warnings": [
            "Registry roles control declared principal IDs but do not authenticate the person operating the account.",
            "This report does not assess factual correctness or general editorial quality.",
        ],
    }


def benchmark_review_quality_report(cursor: Any, review_id: str,
                                    *, now: datetime | None = None) -> dict[str, Any]:
    cursor.execute(
        "SELECT revision, payload_json FROM benchmark_reviews WHERE review_id = %s FOR SHARE",
        (review_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise PostgresBenchmarkQualityError(f"unknown benchmark review: {review_id}")
    report = _benchmark_review_quality_report(cursor, _object(row[1], "benchmark review"), now)
    report["revision"] = row[0]
    return report


def benchmark_acceptance_review_quality_report(cursor: Any, review_id: str,
                                               *, now: datetime | None = None) -> dict[str, Any]:
    cursor.execute(
        "SELECT review_id, execution_id, protocol_record_id, study_id, study_version, "
        "protocol_fingerprint, execution_fingerprint, acceptance_fingerprint, benchmark_id, "
        "benchmark_fingerprint, decision, payload_json FROM benchmark_acceptance_reviews "
        "WHERE review_id = %s FOR SHARE",
        (review_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise PostgresBenchmarkQualityError(f"unknown benchmark acceptance review: {review_id}")
    (stored_id, execution_id, protocol_id, study_id, study_version, protocol_fp,
     execution_fp, acceptance_fp, benchmark_id, benchmark_fp, decision, raw) = row
    payload = _object(raw, "benchmark acceptance review")
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    fingerprint_ok = sha256_fingerprint(payload) == acceptance_fp
    _check(checks, blockers, "acceptance_fingerprint", fingerprint_ok,
           "Acceptance decision fingerprint is verified." if fingerprint_ok else
           "Acceptance decision fingerprint does not match its immutable payload.")

    cursor.execute(
        """SELECT study_id, study_version, benchmark_id, benchmark_fingerprint,
                  rubric_version, protocol_fingerprint, payload_json
           FROM benchmark_study_protocols WHERE protocol_record_id = %s FOR SHARE""",
        (protocol_id,),
    )
    protocol_row = cursor.fetchone()
    protocol = _object(protocol_row[6], "benchmark study protocol") if protocol_row else None
    protocol_ok = bool(
        protocol_row and sha256_fingerprint(protocol) == protocol_row[5] == protocol_fp
        and protocol_row[0] == study_id and protocol_row[1] == study_version
        and protocol_row[2] == benchmark_id and protocol_row[3] == benchmark_fp
    )
    _check(checks, blockers, "registered_protocol", protocol_ok,
           "Exact registered protocol version and fingerprint are verified." if protocol_ok else
           "Registered protocol is missing, changed, or does not match the acceptance decision.")

    cursor.execute(
        """SELECT protocol_record_id, execution_fingerprint, benchmark_id,
                  benchmark_fingerprint, payload_json
           FROM benchmark_study_executions WHERE execution_id = %s FOR SHARE""",
        (execution_id,),
    )
    execution_row = cursor.fetchone()
    execution = _object(execution_row[4], "benchmark study execution") if execution_row else None
    execution_ok = bool(
        execution_row and sha256_fingerprint(execution) == execution_row[1] == execution_fp
        and execution_row[0] == protocol_id and execution_row[2] == benchmark_id
        and execution_row[3] == benchmark_fp
    )
    _check(checks, blockers, "registered_execution", execution_ok,
           "Exact registered execution and protocol linkage are verified." if execution_ok else
           "Registered execution is missing, changed, or not bound to the accepted protocol.")

    cursor.execute(
        """SELECT language, content_fingerprint, content_json
           FROM benchmark_artifacts WHERE artifact_id = %s FOR SHARE""",
        (benchmark_id,),
    )
    artifact_row = cursor.fetchone()
    artifact_ok = bool(
        artifact_row and sha256_fingerprint(_object(artifact_row[2], "benchmark artifact")) == artifact_row[1]
        and artifact_row[1] == benchmark_fp and execution_ok
        and execution_row[2] == benchmark_id and execution_row[3] == benchmark_fp
    )
    _check(checks, blockers, "benchmark_artifact", artifact_ok,
           "Registered benchmark artifact fingerprint is verified." if artifact_ok else
           "Benchmark artifact is missing, changed, or no longer matches the study execution.")
    try:
        if not (protocol_ok and execution_ok and artifact_ok):
            raise ValueError("exact registered study evidence is unavailable")
        from .benchmark_acceptance_review import validate_benchmark_acceptance_review
        contract_ok = bool(validate_benchmark_acceptance_review(
            payload, protocol=protocol, execution=execution)["valid"])
    except (KeyError, TypeError, ValueError, PostgresBenchmarkQualityError):
        contract_ok = False
    _check(checks, blockers, "acceptance_contract", contract_ok,
           "Independent acceptance contract remains valid." if contract_ok else
           "Acceptance decision no longer satisfies its registered study contract.")

    domains = payload.get("domains", {})
    all_domains_pass = (
        set(domains) == {"statistical_validity", "editorial_validity", "operational_security"}
        and all(isinstance(value, dict) and value.get("status") == "pass" for value in domains.values())
    )
    decision_accepted = decision in {"accepted_for_limited_use", "accepted_for_proposed_use"}
    _check(checks, blockers, "accepted_decision", decision_accepted and all_domains_pass,
           "All three independent review domains passed for a scoped accepted decision."
           if decision_accepted and all_domains_pass else
           "Decision is not accepted for scoped use or all three review domains do not pass.")

    cursor.execute(
        """SELECT review_id, revision, payload_json FROM benchmark_reviews
           WHERE benchmark_id = %s ORDER BY review_id FOR SHARE""",
        (benchmark_id,),
    )
    source_rows = cursor.fetchall()
    eligible_reviewers: set[str] = set()
    source_review_checks: list[dict[str, Any]] = []
    for source_review_id, revision, source_raw in source_rows:
        source_review = _object(source_raw, "benchmark source review")
        reviewer_id = source_review.get("reviewer", {}).get("reviewer_id")
        cursor.execute(
            """SELECT active, roles_json, languages_json, principal_kind
               FROM benchmark_principals WHERE principal_id = %s FOR SHARE""",
            (reviewer_id,),
        )
        principal_row = cursor.fetchone() if reviewer_id else None
        principal_roles = json.loads(principal_row[1]) if principal_row else []
        principal_languages = json.loads(principal_row[2]) if principal_row else []
        principal_ok = bool(
            principal_row and principal_row[0] and "benchmark_reviewer" in principal_roles
            and principal_row[3] == "human" and source_review.get("language") in principal_languages
        )
        exact_binding = bool(
            source_review.get("benchmark_id") == benchmark_id
            and source_review.get("benchmark_fingerprint") == benchmark_fp
            and artifact_row and source_review.get("language") == artifact_row[0]
            and source_review.get("reviewer", {}).get("language") == artifact_row[0]
        )
        quality = _benchmark_review_quality_report(cursor, source_review, now)
        cursor.execute(
            """SELECT principal_id, issuer, audience, subject, provider_key_id,
                      provider_adapter, expires_at, attested_at
               FROM benchmark_review_identity_attestations
               WHERE review_id = %s AND review_revision = %s FOR SHARE""",
            (source_review_id, revision),
        )
        attestation = cursor.fetchone()
        attested = bool(
            attestation and attestation[0] == reviewer_id
            and all(attestation[index] for index in (1, 2, 3, 4, 5))
        )
        if attested:
            try:
                expires = datetime.fromisoformat(attestation[6].replace("Z", "+00:00"))
                attested_at = datetime.fromisoformat(attestation[7].replace("Z", "+00:00"))
                attested = expires.tzinfo is not None and attested_at.tzinfo is not None and attested_at < expires
            except (TypeError, ValueError):
                attested = False
        eligible = bool(
            source_review.get("status") == "approved" and principal_ok and exact_binding
            and quality["eligible_for_current_use"] and attested
        )
        if eligible:
            eligible_reviewers.add(reviewer_id)
        source_review_checks.append({
            "review_id": source_review_id, "reviewer_id": reviewer_id,
            "current_permission_eligible": quality["eligible_for_current_use"],
            "active_human_reviewer": principal_ok, "exact_artifact_binding": exact_binding,
            "identity_attested_for_current_revision": attested,
            "eligible_current_source_review": eligible,
        })
    coverage = len(eligible_reviewers) >= 2
    _check(checks, blockers, "current_human_source_reviews", coverage,
           "At least two distinct, active, attested human reviews remain eligible under current source permissions."
           if coverage else
           "At least two distinct, active, attested human reviews with current source permissions are required.")
    scoped = bool(fingerprint_ok and protocol_ok and execution_ok and artifact_ok and contract_ok
                  and decision_accepted and all_domains_pass and coverage)
    return {
        "review_id": stored_id, "study_id": study_id, "study_version": study_version,
        "execution_id": execution_id, "benchmark_id": benchmark_id, "decision": decision,
        "intended_use": payload.get("intended_use"),
        "scope_limitations": payload.get("scope_limitations", []),
        "eligible_for_scoped_reference": scoped, "checks": checks, "blockers": blockers,
        "source_reviews": source_review_checks, "production_ready": False,
        "authorization_granted": False,
        "interpretation": (
            "This report rechecks evidence linkage and current source permissions for editorial context. "
            "It is not production authorization and does not establish factual correctness."
        ),
    }


def benchmark_evaluator_agreement_report(cursor: Any, artifact_id: str, item_id: str) -> dict[str, Any]:
    """Return descriptive agreement for human score sheets on one exact item."""
    cursor.execute(
        """SELECT language, content_fingerprint, content_json FROM benchmark_artifacts
           WHERE artifact_id = %s FOR SHARE""",
        (artifact_id,),
    )
    artifact_row = cursor.fetchone()
    if artifact_row is None:
        raise PostgresBenchmarkQualityError(f"unknown benchmark artifact: {artifact_id}")
    artifact = _object(artifact_row[2], "benchmark artifact")
    if sha256_fingerprint(artifact) != artifact_row[1]:
        raise PostgresBenchmarkQualityError("registered benchmark artifact fingerprint verification failed")
    cursor.execute(
        """SELECT score_id, artifact_fingerprint, evaluator_id, issuer, subject,
                  provider_key_id, payload_json
           FROM benchmark_scores WHERE artifact_id = %s AND item_id = %s
           ORDER BY created_at, score_id FOR SHARE""",
        (artifact_id, item_id),
    )
    rows = cursor.fetchall()
    if not rows:
        raise PostgresBenchmarkQualityError("no persisted score sheets exist for this benchmark item")
    scores: list[dict[str, Any]] = []
    for score_id, artifact_fp, evaluator_id, issuer, subject, key_id, raw in rows:
        if artifact_fp != artifact_row[1]:
            raise PostgresBenchmarkQualityError("stored score does not match the registered artifact fingerprint")
        cursor.execute(
            """SELECT principal_kind FROM benchmark_principals
               WHERE principal_id = %s FOR SHARE""",
            (evaluator_id,),
        )
        principal = cursor.fetchone()
        if principal is None or principal[0] != "human":
            raise PostgresBenchmarkQualityError("agreement reports require human-classified evaluator principals")
        if not issuer or not subject or not key_id:
            raise PostgresBenchmarkQualityError("agreement report requires provider-attested evaluator provenance")
        score = _object(raw, "benchmark score")
        if sha256_fingerprint(score) != _score_fingerprint(cursor, score_id):
            raise PostgresBenchmarkQualityError("stored score fingerprint verification failed")
        scores.append(score)
    from .benchmark_agreement import BenchmarkAgreementError, benchmark_evaluator_agreement
    try:
        return benchmark_evaluator_agreement(scores)
    except BenchmarkAgreementError as exc:
        raise PostgresBenchmarkQualityError(str(exc)) from exc


def _score_fingerprint(cursor: Any, score_id: str) -> str:
    cursor.execute(
        "SELECT score_fingerprint FROM benchmark_scores WHERE score_id = %s FOR SHARE",
        (score_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise PostgresBenchmarkQualityError("benchmark score disappeared during agreement evaluation")
    return row[0]


def benchmark_readiness_report(cursor: Any, artifact_id: str,
                               *, now: datetime | None = None) -> dict[str, Any]:
    """Recheck review, reviewer, identity-attestation, and adjudication readiness."""
    cursor.execute(
        """SELECT language, content_fingerprint, content_json FROM benchmark_artifacts
           WHERE artifact_id = %s FOR SHARE""",
        (artifact_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise PostgresBenchmarkQualityError(f"unknown benchmark artifact: {artifact_id}")
    artifact = {
        "artifact_id": artifact_id, "language": row[0], "content_fingerprint": row[1],
        "fingerprint_verified": sha256_fingerprint(_object(row[2], "benchmark artifact")) == row[1],
    }
    cursor.execute(
        """SELECT review_id, revision, payload_json FROM benchmark_reviews
           WHERE benchmark_id = %s ORDER BY review_id FOR SHARE""",
        (artifact_id,),
    )
    review_rows = cursor.fetchall()
    reviews = [(_object(item[2], "benchmark review"), item[1]) for item in review_rows]
    principals: dict[str, dict[str, Any] | None] = {}
    for review, _ in reviews:
        reviewer_id = review.get("reviewer", {}).get("reviewer_id")
        if reviewer_id and reviewer_id not in principals:
            cursor.execute(
                """SELECT active, roles_json, languages_json, principal_kind
                   FROM benchmark_principals WHERE principal_id = %s FOR SHARE""",
                (reviewer_id,),
            )
            principal_row = cursor.fetchone()
            principals[reviewer_id] = None if principal_row is None else {
                "active": bool(principal_row[0]), "roles": json.loads(principal_row[1]),
                "languages": json.loads(principal_row[2]), "principal_kind": principal_row[3],
            }
    eligible = []
    review_checks = []
    for review, revision in reviews:
        reviewer_id = review.get("reviewer", {}).get("reviewer_id")
        principal = principals.get(reviewer_id)
        quality = _benchmark_review_quality_report(cursor, review, now)
        principal_ok = bool(
            principal and principal["active"] and "benchmark_reviewer" in principal["roles"]
            and artifact["language"] in principal["languages"]
        )
        binding_ok = (
            review.get("benchmark_fingerprint") == artifact["content_fingerprint"]
            and review.get("language") == artifact["language"]
        )
        cursor.execute(
            """SELECT principal_id, issuer, audience, subject, provider_key_id,
                      provider_adapter, expires_at, attested_at
               FROM benchmark_review_identity_attestations
               WHERE review_id = %s AND review_revision = %s FOR SHARE""",
            (review.get("review_id"), revision),
        )
        attestation = cursor.fetchone()
        identity_attested = bool(
            attestation and attestation[0] == reviewer_id and all(attestation[i] for i in (1, 2, 3, 4, 5))
        )
        if identity_attested:
            try:
                expires = datetime.fromisoformat(attestation[6].replace("Z", "+00:00"))
                attested_at = datetime.fromisoformat(attestation[7].replace("Z", "+00:00"))
                identity_attested = expires.tzinfo is not None and attested_at.tzinfo is not None and attested_at < expires
            except (TypeError, ValueError):
                identity_attested = False
        current = bool(
            review.get("status") == "approved" and quality["eligible_for_current_use"]
            and principal_ok and binding_ok
        )
        if current:
            eligible.append((review, principal, identity_attested))
        review_checks.append({
            "review_id": review.get("review_id"), "reviewer_id": reviewer_id,
            "status": review.get("status"),
            "principal_kind": principal["principal_kind"] if principal else "missing",
            "active_qualified_principal": principal_ok, "exact_artifact_binding": binding_ok,
            "current_review_policy_eligible": quality["eligible_for_current_use"],
            "identity_provider_attested": identity_attested,
            "eligible_approved_review": current,
        })
    distinct_reviewers = {review["reviewer"]["reviewer_id"] for review, _, _ in eligible}
    kinds = {principal["principal_kind"] for _, principal, _ in eligible}
    attested_reviewers = {
        review["reviewer"]["reviewer_id"] for review, principal, attested in eligible
        if principal["principal_kind"] == "human" and attested
    }
    cursor.execute(
        "SELECT adjudication_id FROM benchmark_adjudications WHERE benchmark_id = %s FOR SHARE",
        (artifact_id,),
    )
    adjudication_rows = cursor.fetchall()
    blockers = []
    if not artifact["fingerprint_verified"]:
        blockers.append("Artifact fingerprint verification failed.")
    if len(distinct_reviewers) < 2:
        blockers.append("At least two distinct active, language-qualified reviewers with currently eligible approved reviews are required.")
    if any(kind != "human" for kind in kinds):
        blockers.append("Eligible reviews include synthetic, automated, or unverified principals.")
    if len(attested_reviewers) < 2:
        blockers.append("At least two eligible human reviews require immutable identity-provider attestations bound to their exact review revisions.")
    if len(adjudication_rows) == 0:
        blockers.append("No independent final adjudication is recorded.")
    if len(adjudication_rows) > 1:
        blockers.append("Conflicting final adjudications exist.")
    if not reviews and not eligible:
        status = "human_review_pending"
    elif eligible and kinds <= {"synthetic", "automation"}:
        status = "synthetic_only"
    elif len(distinct_reviewers) >= 2 and len(attested_reviewers) >= 2 and kinds == {"human"} and not adjudication_rows:
        status = "ready_for_adjudication"
    else:
        status = "human_review_pending"
    return {
        "artifact": artifact, "status": status,
        "eligible_approved_review_count": len(eligible),
        "distinct_eligible_reviewer_count": len(distinct_reviewers),
        "reviewer_classifications": sorted(kinds), "reviews": review_checks,
        "adjudication_count": len(adjudication_rows), "blockers": blockers,
        "production_ready": False,
        "warnings": [
            "principal_kind is an administrative classification; identity attestation depends on the configured provider adapter verifying issuer, audience, signature/key, session revocation, and subject-to-principal mapping.",
            "This report does not compare review content to determine whether reviewers disagree.",
            "Only the existing review, permission, and adjudication policy gates can authorize current use; this report grants no approval.",
        ],
    }
