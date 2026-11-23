"""Deterministic PostgreSQL learning-object quality gates.

This is a read-only port of SQLiteRegistry.learning_object_quality_report.
Every current learning object, claim, evidence and glossary row is held under a
shared lock for the caller's staging transaction.
"""
from __future__ import annotations

import json
from typing import Any

from .registry import _language_review_passes, _mcq_review_passes


class PostgresLearningQualityError(RuntimeError):
    """Raised when learning-object evidence cannot be verified safely."""


def _object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PostgresLearningQualityError(f"{label} is corrupt JSON") from exc
    if not isinstance(value, dict):
        raise PostgresLearningQualityError(f"{label} must be a JSON object")
    return value


def _check(
    checks: list[dict[str, Any]],
    blockers: list[dict[str, str]],
    warnings: list[dict[str, str]],
    name: str,
    passed: bool,
    detail: str,
    *,
    blocking: bool = True,
) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    if not passed:
        target = blockers if blocking else warnings
        target.append({"check": name, "detail": detail})


def _validate_answer_evidence(cursor: Any, payload: dict[str, Any]) -> None:
    if payload.get("kind") not in {"comprehension_question", "analysis_question", "mcq_question"}:
        return
    content = payload.get("content", {})
    raw_answer_claim_ids = content.get("answer_claim_ids", [])
    raw_object_claim_ids = payload.get("claim_ids", [])
    references = content.get("answer_evidence", [])
    if (
        not isinstance(raw_answer_claim_ids, list)
        or not raw_answer_claim_ids
        or any(not isinstance(item, str) or not item for item in raw_answer_claim_ids)
        or not isinstance(raw_object_claim_ids, list)
        or any(not isinstance(item, str) or not item for item in raw_object_claim_ids)
        or not isinstance(references, list)
        or any(not isinstance(item, dict) for item in references)
    ):
        raise PostgresLearningQualityError("question answer evidence references are malformed")
    answer_claim_ids = set(raw_answer_claim_ids)
    object_claim_ids = set(raw_object_claim_ids)
    if not answer_claim_ids or not references:
        raise PostgresLearningQualityError("question answer evidence is required")
    if any(
        not isinstance(item.get("claim_id"), str) or not item.get("claim_id")
        or not isinstance(item.get("evidence_id"), str) or not item.get("evidence_id")
        for item in references
    ):
        raise PostgresLearningQualityError("question answer evidence IDs are malformed")
    reference_claim_ids = {item["claim_id"] for item in references}
    reference_pairs = [(item["claim_id"], item["evidence_id"]) for item in references]
    if (
        len(raw_answer_claim_ids) != len(answer_claim_ids)
        or reference_claim_ids != answer_claim_ids
        or not answer_claim_ids.issubset(object_claim_ids)
        or len(reference_pairs) != len(set(reference_pairs))
    ):
        raise PostgresLearningQualityError(
            "question answer evidence must uniquely cover every answer claim"
        )
    for item in references:
        claim_id, evidence_id = item["claim_id"], item["evidence_id"]
        expected_revision = item.get("evidence_revision")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise PostgresLearningQualityError(
                "question answer evidence must pin a positive evidence revision"
            )
        cursor.execute(
            """SELECT payload_json FROM domain_records
               WHERE record_type = 'claim' AND id = %s FOR SHARE""",
            (claim_id,),
        )
        claim_row = cursor.fetchone()
        cursor.execute(
            """SELECT revision, payload_json FROM domain_records
               WHERE record_type = 'evidence' AND id = %s FOR SHARE""",
            (evidence_id,),
        )
        evidence_row = cursor.fetchone()
        if claim_row is None or evidence_row is None:
            raise PostgresLearningQualityError(
                "question answer evidence references an unknown claim or evidence record"
            )
        if evidence_row[0] != expected_revision:
            raise PostgresLearningQualityError("question answer evidence revision is stale")
        claim = _object(claim_row[0], "claim")
        evidence = _object(evidence_row[1], "evidence")
        if evidence.get("claim_id") != claim_id:
            raise PostgresLearningQualityError(
                "question answer evidence must belong to its referenced answer claim"
            )
        if evidence_id not in claim.get("evidence_ids", []):
            raise PostgresLearningQualityError(
                "question answer evidence is no longer linked from its claim"
            )
        if evidence.get("relationship") != "supports":
            raise PostgresLearningQualityError(
                "question answer evidence must support its referenced answer claim"
            )
        provenance_matches = any(
            provenance.get("source_id") == evidence.get("source_id")
            and (
                evidence.get("article_id") is None
                or provenance.get("article_id") == evidence.get("article_id")
            )
            and (
                evidence.get("observation_id") is None
                or provenance.get("observation_id") == evidence.get("observation_id")
            )
            for provenance in claim.get("provenance", [])
        )
        if not provenance_matches or not evidence.get("locator", {}).get("uri"):
            raise PostgresLearningQualityError(
                "question answer evidence locator must match current claim provenance"
            )


def _validate_source_evidence(cursor: Any, payload: dict[str, Any]) -> None:
    if payload.get("kind") != "vocabulary":
        return
    content = payload["content"]
    evidence_id = content.get("source_evidence_id")
    expected_revision = content.get("source_evidence_revision")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise PostgresLearningQualityError("vocabulary source evidence ID is required")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 1
    ):
        raise PostgresLearningQualityError("vocabulary source evidence revision is required")
    cursor.execute(
        """SELECT revision, payload_json FROM domain_records
           WHERE record_type = 'evidence' AND id = %s FOR SHARE""",
        (evidence_id,),
    )
    evidence_row = cursor.fetchone()
    if evidence_row is None:
        raise PostgresLearningQualityError(
            "vocabulary learning object references an unknown source evidence record"
        )
    if evidence_row[0] != expected_revision:
        raise PostgresLearningQualityError("vocabulary source evidence revision is stale")
    evidence = _object(evidence_row[1], "evidence")
    claim_ids = payload.get("claim_ids", [])
    if evidence.get("claim_id") not in claim_ids:
        raise PostgresLearningQualityError(
            "vocabulary source evidence must belong to one of the learning object's claims"
        )
    cursor.execute(
        """SELECT payload_json FROM domain_records
           WHERE record_type = 'claim' AND id = %s FOR SHARE""",
        (evidence["claim_id"],),
    )
    claim_row = cursor.fetchone()
    if claim_row is None:
        raise PostgresLearningQualityError("vocabulary source evidence claim is missing")
    claim = _object(claim_row[0], "claim")
    if evidence_id not in claim.get("evidence_ids", []):
        raise PostgresLearningQualityError(
            "vocabulary source evidence is no longer linked from its claim"
        )
    provenance_matches = any(
        provenance.get("source_id") == evidence.get("source_id")
        and (
            evidence.get("article_id") is None
            or provenance.get("article_id") == evidence.get("article_id")
        )
        and (
            evidence.get("observation_id") is None
            or provenance.get("observation_id") == evidence.get("observation_id")
        )
        for provenance in claim.get("provenance", [])
    )
    if not provenance_matches or not evidence.get("locator"):
        raise PostgresLearningQualityError(
            "vocabulary source evidence locator must match the claim's current provenance"
        )


def load_postgres_learning_object_quality_report(
    cursor: Any, learning_object_id: str
) -> dict[str, Any]:
    cursor.execute(
        """SELECT revision, payload_json FROM learning_objects
           WHERE id = %s FOR SHARE""",
        (learning_object_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise PostgresLearningQualityError(f"unknown learning object: {learning_object_id}")
    revision, raw_payload = row
    payload = _object(raw_payload, "learning object")
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    _check(
        checks, blockers, warnings, "editorial_review",
        payload["status"] == "approved",
        "Learning object must be approved before it can pass the registry quality gate.",
    )
    language_review_valid = _language_review_passes(
        payload.get("language_review"), payload.get("language")
    )
    _check(
        checks, blockers, warnings, "language_review", language_review_valid,
        "A complete language-specific rubric review must pass before a learning object can pass the registry quality gate.",
    )
    mcq_review_valid = _mcq_review_passes(
        payload.get("mcq_review"), payload.get("language")
    )
    _check(
        checks, blockers, warnings, "mcq_pedagogical_review",
        payload.get("kind") != "mcq_question"
        or (payload.get("status") != "approved" or mcq_review_valid),
        "An approved MCQ requires a complete all-pass pedagogical review rubric.",
    )

    claim_ids = payload.get("claim_ids", [])
    if claim_ids:
        cursor.execute(
            """SELECT id, payload_json FROM domain_records
               WHERE record_type = 'claim' AND id = ANY(%s)
               FOR SHARE""",
            (claim_ids,),
        )
        claim_rows = cursor.fetchall()
    else:
        claim_rows = []
    claims = {item[0]: _object(item[1], "claim") for item in claim_rows}
    missing_claims = sorted(set(claim_ids) - set(claims))
    _check(
        checks, blockers, warnings, "claim_references", not missing_claims,
        "All linked claims must still exist." if not missing_claims
        else "Missing claims: " + ", ".join(missing_claims),
    )

    claim_evidence: dict[str, list[str]] = {}
    claim_assessments: dict[str, str] = {}
    all_evidence_ids: set[str] = set()
    for claim_id, claim in claims.items():
        evidence_ids = claim.get("evidence_ids", [])
        all_evidence_ids.update(evidence_ids)
        claim_evidence[claim_id] = evidence_ids
        claim_assessments[claim_id] = claim.get("assessment", "unassessed")
        if not claim.get("provenance", []):
            blockers.append({
                "check": "claim_provenance",
                "detail": f"Claim {claim_id} has no source provenance.",
            })
        if claim.get("assessment") in {"supported", "partially_supported"} and not evidence_ids:
            blockers.append({
                "check": "claim_evidence",
                "detail": f"Claim {claim_id} is assessed but has no linked evidence.",
            })
    found_evidence_ids: set[str] = set()
    if all_evidence_ids:
        cursor.execute(
            """SELECT id FROM domain_records
               WHERE record_type = 'evidence' AND id = ANY(%s)
               FOR SHARE""",
            (sorted(all_evidence_ids),),
        )
        found_evidence_ids = {item[0] for item in cursor.fetchall()}
    for claim_id, evidence_ids in claim_evidence.items():
        if set(evidence_ids) - found_evidence_ids:
            blockers.append({
                "check": "claim_evidence",
                "detail": f"Claim {claim_id} has missing evidence records.",
            })
    _check(
        checks, blockers, warnings, "claim_provenance",
        all(claim.get("provenance") for claim in claims.values()) and not missing_claims,
        "Every linked claim has source provenance." if claims and not missing_claims
        and all(claim.get("provenance") for claim in claims.values())
        else "One or more linked claims lack source provenance.",
    )

    factual_claims: set[str] = set()
    if payload["kind"] in {"headline_summary", "brief_summary", "detailed_summary", "revision_card"}:
        for segment in payload["content"].get("segments", []):
            if segment["role"] == "fact":
                factual_claims.update(segment["claim_ids"])
                for claim_id in segment["claim_ids"]:
                    assessment = claim_assessments.get(claim_id, "unassessed")
                    if assessment not in {"supported", "partially_supported"}:
                        blockers.append({
                            "check": "factual_segment_assessment",
                            "detail": f"Factual segment claim {claim_id} is assessed as {assessment}, not supported.",
                        })
                    elif not claim_evidence.get(claim_id):
                        blockers.append({
                            "check": "factual_segment_evidence",
                            "detail": f"Factual segment claim {claim_id} has no linked evidence.",
                        })
            elif segment["role"] in {"attributed", "analysis"}:
                for claim_id in segment["claim_ids"]:
                    assessment = claim_assessments.get(claim_id, "unassessed")
                    if assessment in {"disputed", "contradicted", "retracted", "unassessed"}:
                        warnings.append({
                            "check": "qualified_segment",
                            "detail": f"{segment['role'].title()} segment cites claim {claim_id} with assessment {assessment}; preserve that qualification in the wording.",
                        })
    _check(
        checks, blockers, warnings, "factual_segments",
        all(
            claim_assessments.get(claim_id) in {"supported", "partially_supported"}
            and bool(claim_evidence.get(claim_id))
            for claim_id in factual_claims
        ),
        "Every factual summary segment cites assessed claims with linked evidence.",
    )

    if payload["kind"] in {"comprehension_question", "analysis_question", "mcq_question"}:
        answer_claims = payload["content"].get("answer_claim_ids", [])
        answer_supported = bool(answer_claims) and all(
            claim_assessments.get(claim_id) in {"supported", "partially_supported"}
            and bool(claim_evidence.get(claim_id))
            for claim_id in answer_claims
        )
        _check(
            checks, blockers, warnings, "answer_support", answer_supported,
            "Every answer claim must be supported or partially supported and have linked evidence.",
        )
        if payload["kind"] == "mcq_question":
            options = payload["content"].get("options", [])
            correct_option_id = payload["content"].get("correct_option_id")
            distractor_ids = {item["id"] for item in options} - {correct_option_id}
            rationale_ids = [
                item.get("option_id")
                for item in payload["content"].get("distractor_rationales", [])
            ]
            rationale_coverage = (
                len(rationale_ids) == len(set(rationale_ids))
                and set(rationale_ids) == distractor_ids
            )
            _check(
                checks, blockers, warnings, "mcq_distractor_rationale_coverage",
                rationale_coverage,
                "Every incorrect option must have exactly one rationale; the correct option must not have a distractor rationale.",
            )
            rationale_claim_ids = {
                claim_id
                for item in payload["content"].get("distractor_rationales", [])
                for claim_id in item.get("basis_claim_ids", [])
            }
            rationale_claims_supported = bool(rationale_claim_ids) and all(
                claim_assessments.get(claim_id) in {"supported", "partially_supported"}
                and bool(claim_evidence.get(claim_id))
                for claim_id in rationale_claim_ids
            )
            _check(
                checks, blockers, warnings, "mcq_distractor_rationale_source_support",
                rationale_claims_supported,
                "Every distractor rationale must link to story claims assessed as supported or partially supported with evidence.",
            )
            warnings.append({
                "check": "mcq_human_distractor_review",
                "detail": (
                    "Human review is still required to judge whether distractors are plausible, "
                    "unambiguously incorrect under the question wording, misconception-based, "
                    "parallel in form, and free of accidental clues. Rationale presence does not "
                    "prove distractor quality."
                ),
            })
        try:
            _validate_answer_evidence(cursor, payload)
            answer_evidence_valid = True
            answer_evidence_detail = "Every answer claim has a current supporting evidence locator."
        except PostgresLearningQualityError as exc:
            answer_evidence_valid = False
            answer_evidence_detail = str(exc)
        _check(
            checks, blockers, warnings, "answer_evidence",
            answer_evidence_valid, answer_evidence_detail,
        )

    if payload["kind"] == "vocabulary":
        try:
            _validate_source_evidence(cursor, payload)
            source_evidence_valid = True
            source_evidence_detail = (
                "The vocabulary source evidence exists, remains linked to an object claim, "
                "and matches current claim provenance."
            )
        except PostgresLearningQualityError as exc:
            source_evidence_valid = False
            source_evidence_detail = str(exc)
        _check(
            checks, blockers, warnings, "vocabulary_source_evidence",
            source_evidence_valid, source_evidence_detail,
        )

        glossary_id = payload["content"]["glossary_entry_id"]
        cursor.execute(
            "SELECT status, payload_json FROM glossary_entries WHERE id = %s FOR SHARE",
            (glossary_id,),
        )
        glossary_row = cursor.fetchone()
        glossary_valid = False
        if glossary_row:
            glossary = _object(glossary_row[1], "glossary entry")
            glossary_valid = (
                glossary_row[0] == "approved"
                and glossary["lemma"] == payload["content"]["term"]
                and glossary["term_language"] == payload["content"]["term_language"]
                and glossary["part_of_speech"] == payload["content"]["part_of_speech"]
                and any(
                    sense["language"] == payload["language"]
                    and sense["definition"] == payload["content"]["meaning"]
                    for sense in glossary["senses"]
                )
            )
        _check(
            checks, blockers, warnings, "controlled_glossary", glossary_valid,
            "Vocabulary must match an approved glossary entry and a sense in the learning object's language.",
        )

    return {
        "learning_object_id": learning_object_id,
        "revision": revision,
        "status": payload["status"],
        "meets_registry_quality_gates": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
    }
