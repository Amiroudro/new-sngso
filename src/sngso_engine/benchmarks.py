"""Deterministic golden-fixture checks for editorial regression tests.

These checks compare an artifact with an explicitly curated fixture. They do
not measure general language quality, factual truth, or semantic equivalence.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any


def _normalise_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", value).split())


def _segment_view(segments: Any) -> list[dict[str, Any]]:
    if not isinstance(segments, list):
        return []
    result: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            return []
        claims = segment.get("claim_ids", [])
        result.append({
            "text": _normalise_text(segment.get("text")),
            "claim_ids": list(claims) if isinstance(claims, list) else [],
            "role": segment.get("role"),
            "attribution": segment.get("attribution"),
        })
    return result


def evaluate_editorial_benchmark(
    benchmark: Mapping[str, Any],
    learning_object: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare one learning object to a synthetic, exact-output regression case.

    A passing result means the artifact matches this fixture's explicit
    expectations. It is not a general quality score or truth assessment.
    """
    if not isinstance(benchmark, Mapping) or not isinstance(learning_object, Mapping):
        raise TypeError("benchmark and learning_object must be mappings")
    expected = benchmark.get("expected")
    if not isinstance(expected, Mapping):
        raise ValueError("benchmark.expected must be an object")

    content = learning_object.get("content")
    if not isinstance(content, Mapping):
        content = {}
    actual_text = _normalise_text(content.get("text"))
    expected_text = _normalise_text(expected.get("text"))
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    add("fixture_language", learning_object.get("language") == benchmark.get("language"),
        "Artifact language must match the benchmark language.")
    add("fixture_kind", learning_object.get("kind") == benchmark.get("kind"),
        "Artifact kind must match the benchmark kind.")
    add("expected_text", actual_text == expected_text,
        "Artifact text must match curated expected text after NFC and whitespace normalisation.")

    expected_segments = _segment_view(expected.get("segments"))
    actual_segments = _segment_view(content.get("segments"))
    add("segment_traceability", bool(expected_segments) and actual_segments == expected_segments,
        "Ordered segment text, role, attribution, and claim links must match the fixture.")

    expected_claims = expected.get("claim_ids", [])
    actual_claims = learning_object.get("claim_ids", [])
    add("claim_coverage", isinstance(expected_claims, list) and isinstance(actual_claims, list)
        and actual_claims == expected_claims,
        "Artifact claim links must match the fixture's explicit claim coverage.")

    required = benchmark.get("must_preserve", [])
    required_ok = isinstance(required, list) and all(
        isinstance(item, str) and _normalise_text(item) in actual_text for item in required
    )
    add("must_preserve", required_ok,
        "Every required qualification or detail must remain in the artifact.")

    prohibited = benchmark.get("must_not_contain", [])
    prohibited_ok = isinstance(prohibited, list) and all(
        isinstance(item, str) and _normalise_text(item).casefold() not in actual_text.casefold()
        for item in prohibited
    )
    add("unsupported_detail", prohibited_ok,
        "The artifact must not introduce fixture-listed unsupported assertions.")

    failures = [item for item in checks if not item["passed"]]
    return {
        "benchmark_id": benchmark.get("id"),
        "fixture_type": benchmark.get("fixture_type"),
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "scope_note": (
            "Synthetic exact-output regression only; this result is not human "
            "language approval, a truth score, or a general semantic-quality measure."
        ),
    }
