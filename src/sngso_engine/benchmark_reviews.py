"""Validation helpers for human-review metadata on editorial benchmarks.

This module validates declarations; it does not authenticate reviewers, verify
publisher rights, or persist an immutable review history.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


def _aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _https_uri(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_human_benchmark_review(review: Mapping[str, Any]) -> dict[str, Any]:
    """Return named validation checks; approval requires explicit human evidence.

    A passing report validates the shape and internal consistency of declared
    review metadata only. Reviewer identity and rights references need external
    verification before operational use.
    """
    if not isinstance(review, Mapping):
        raise TypeError("review must be a mapping")

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    status = review.get("status")
    language = review.get("language")
    reviewer = review.get("reviewer")
    reviewer = reviewer if isinstance(reviewer, Mapping) else {}
    sources = review.get("source_materials")
    sources = sources if isinstance(sources, list) else []
    corrections = review.get("corrections")
    corrections = corrections if isinstance(corrections, list) else []

    add("status", isinstance(status, str) and status in {"draft", "approved", "rejected"},
        "Status must be draft, approved, or rejected.")
    add("benchmark_fingerprint", isinstance(review.get("benchmark_fingerprint"), str)
        and bool(_FINGERPRINT.fullmatch(review["benchmark_fingerprint"])),
        "Review must bind to the exact benchmark content fingerprint.")
    add("review_timestamp", _aware_timestamp(review.get("reviewed_at")),
        "Review timestamp must include a timezone.")
    add("reviewer_identity", isinstance(reviewer.get("reviewer_id"), str)
        and bool(reviewer.get("reviewer_id", "").strip())
        and isinstance(reviewer.get("role"), str) and bool(reviewer.get("role", "").strip()),
        "Reviewer ID and role must be recorded.")
    add("reviewer_language", isinstance(language, str) and language in {"bn", "en"} and reviewer.get("language") == language,
        "Reviewer language must match the benchmark language.")
    add("reviewer_qualification", isinstance(reviewer.get("qualification_basis"), str)
        and len(reviewer.get("qualification_basis", "").strip()) >= 20,
        "A meaningful qualification basis must be recorded.")

    source_checks = bool(sources) or status != "approved"
    for source in sources:
        if not isinstance(source, Mapping):
            source_checks = False
            break
        source_checks = source_checks and all(
            isinstance(source.get(key), str) and bool(source.get(key, "").strip())
            for key in (
                "article_id", "source_id", "approved_use_reference",
                "permission_grant_id", "endpoint_id",
            )
        ) and source.get("permission_operation") in {
            "excerpt_storage", "full_text_storage", "translation"
        } and _https_uri(source.get("canonical_uri")) \
          and isinstance(source.get("content_fingerprint"), str) \
          and bool(_FINGERPRINT.fullmatch(source.get("content_fingerprint", ""))) \
          and _aware_timestamp(source.get("checked_at"))
    add("approved_source_material", source_checks,
        "Approved reviews require at least one HTTPS source article, source identity, content fingerprint, checked timestamp, and approved-use reference.")

    correction_checks = all(
        isinstance(item, Mapping)
        and all(isinstance(item.get(key), str) for key in ("field", "original", "corrected", "rationale"))
        and bool(item.get("field", "").strip())
        and bool(item.get("rationale", "").strip())
        for item in corrections
    )
    add("correction_log", correction_checks,
        "Every correction must retain the field, original text, corrected text, and rationale.")

    note = review.get("adjudication_note")
    add("adjudication_note", isinstance(note, str) and (status != "approved" or bool(note.strip())),
        "Approved benchmarks require an adjudication note.")

    failures = [item for item in checks if not item["passed"]]
    approved = status == "approved" and not failures
    return {
        "review_id": review.get("review_id"),
        "benchmark_id": review.get("benchmark_id"),
        "status": status,
        "valid": not failures,
        "approval_eligible": approved,
        "checks": checks,
        "failures": failures,
        "scope_note": (
            "Metadata validation only. It does not authenticate reviewer identity, "
            "verify source-use rights, assess editorial correctness, or create an "
            "immutable registry review history."
        ),
    }
