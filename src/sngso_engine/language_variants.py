"""Fail-closed checks for independently reviewed Bangla/English variants.

This verifies registry evidence for a paired editorial artifact. It does not
authenticate an OIDC token, create a human review, or authorize publication.
"""
from __future__ import annotations

from typing import Any


class BilingualVariantError(ValueError):
    """Raised when a bilingual pair lacks independent, language-qualified review evidence."""


def verify_independent_bilingual_variants(
    registry: Any,
    bangla_learning_object_id: str,
    english_learning_object_id: str,
) -> dict[str, Any]:
    """Verify a Bangla/English pair with distinct provider-attested human reviewers.

    Both records must be approved, share story/kind/variant group and claim
    coverage, pass their own language rubric, and have a reviewer principal
    qualified for that language. Reviewer identity is compared by exact
    (issuer, subject), not merely by the local principal ID.
    """
    if (
        not isinstance(bangla_learning_object_id, str)
        or not bangla_learning_object_id.strip()
        or not isinstance(english_learning_object_id, str)
        or not english_learning_object_id.strip()
    ):
        raise BilingualVariantError("both learning-object IDs are required")
    if bangla_learning_object_id == english_learning_object_id:
        raise BilingualVariantError("Bangla and English variants must be separate records")

    records = {}
    payloads = {}
    identities = {}
    for language, object_id in (("bn", bangla_learning_object_id), ("en", english_learning_object_id)):
        record = registry.get_learning_object(object_id)
        if record is None:
            raise BilingualVariantError(f"{language} learning object is not registered")
        payload = record.to_payload()
        if payload.get("language") != language:
            raise BilingualVariantError(f"expected a {language} learning object")
        if payload.get("status") != "approved":
            raise BilingualVariantError(f"{language} learning object is not approved")
        report = registry.learning_object_quality_report(object_id)
        if not report.get("meets_registry_quality_gates"):
            raise BilingualVariantError(f"{language} learning object failed its registry quality gates")

        review = payload.get("language_review")
        if not isinstance(review, dict) or review.get("language") != language:
            raise BilingualVariantError(f"{language} variant lacks its own language-specific review")
        if review.get("reviewer_id") != payload.get("reviewed_by"):
            raise BilingualVariantError(f"{language} reviewer does not match the approval record")
        if review.get("reviewed_at") != payload.get("reviewed_at"):
            raise BilingualVariantError(f"{language} review timestamp does not match the approval record")
        dimensions = review.get("dimensions")
        if not isinstance(dimensions, dict) or dimensions.get("translation_fidelity") != "pass":
            raise BilingualVariantError(f"{language} translation-fidelity review must explicitly pass")

        attestation = review.get("identity_attestation")
        if not isinstance(attestation, dict):
            raise BilingualVariantError(f"{language} review lacks provider identity attestation")
        principal_id = review.get("reviewer_id")
        if attestation.get("principal_id") != principal_id:
            raise BilingualVariantError(f"{language} attestation principal does not match its reviewer")
        issuer = attestation.get("issuer")
        subject = attestation.get("subject")
        if not isinstance(issuer, str) or not issuer.strip() or not isinstance(subject, str) or not subject.strip():
            raise BilingualVariantError(f"{language} review identity is incomplete")
        if attestation.get("attested_at") != review.get("reviewed_at"):
            raise BilingualVariantError(f"{language} identity attestation timestamp does not match its review")

        principal = registry.get_benchmark_principal(principal_id)
        if (
            principal is None
            or not principal.get("active")
            or principal.get("principal_kind") != "human"
            or "benchmark_reviewer" not in principal.get("roles", [])
            or language not in principal.get("languages", [])
        ):
            raise BilingualVariantError(
                f"{language} reviewer must be an active human benchmark reviewer qualified for {language}"
            )

        records[language] = record
        payloads[language] = payload
        identities[language] = (issuer, subject)

    bangla = payloads["bn"]
    english = payloads["en"]
    if bangla.get("story_id") != english.get("story_id"):
        raise BilingualVariantError("language variants must belong to the same story")
    if bangla.get("kind") != english.get("kind"):
        raise BilingualVariantError("language variants must have the same learning-object kind")
    group_id = bangla.get("variant_group_id")
    if not isinstance(group_id, str) or not group_id.strip() or group_id != english.get("variant_group_id"):
        raise BilingualVariantError("language variants must share a non-empty variant group")
    if set(bangla.get("claim_ids", [])) != set(english.get("claim_ids", [])):
        raise BilingualVariantError("language variants must cover the same registered claims")
    if identities["bn"] == identities["en"]:
        raise BilingualVariantError("Bangla and English variants must have different authenticated reviewer identities")

    return {
        "status": "verified_for_bilingual_staging_only",
        "publication_authorized": False,
        "story_id": bangla["story_id"],
        "kind": bangla["kind"],
        "variant_group_id": group_id,
        "bangla_learning_object_id": bangla_learning_object_id,
        "bangla_revision": records["bn"].revision,
        "english_learning_object_id": english_learning_object_id,
        "english_revision": records["en"].revision,
        "claim_ids": sorted(bangla.get("claim_ids", [])),
        "independent_reviewer_identities": True,
    }
