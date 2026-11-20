"""Descriptive evaluator-agreement reports for matched benchmark score sheets.

These statistics describe observed score differences. They are not reliability
coefficients and do not establish evaluator competence or editorial truth.
"""
from __future__ import annotations

from decimal import Decimal
from itertools import combinations
from typing import Any


class BenchmarkAgreementError(ValueError):
    """Raised when score sheets cannot be compared on a common basis."""


def benchmark_evaluator_agreement(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize independent evaluator score differences on one exact item."""
    if not isinstance(scores, list) or len(scores) < 2:
        raise BenchmarkAgreementError("at least two score sheets are required")
    identities = [row.get("evaluator_id") for row in scores]
    if any(not isinstance(value, str) or not value.strip() for value in identities):
        raise BenchmarkAgreementError("every score sheet needs an evaluator identity")
    if len(set(identities)) != len(identities):
        raise BenchmarkAgreementError("each evaluator may contribute only one score sheet")
    first = scores[0]
    binding = ("benchmark_id", "benchmark_fingerprint", "item_id", "task_type", "language")
    if any(any(row.get(key) != first.get(key) for key in binding) for row in scores[1:]):
        raise BenchmarkAgreementError("score sheets must bind to the same artifact, fingerprint, item, task, and language")
    if any(row.get("critical_failure", False) for row in scores):
        raise BenchmarkAgreementError("critical-failure score sheets are not eligible for agreement comparison")
    maps: list[dict[str, dict[str, Any]]] = []
    for row in scores:
        criteria = row.get("criterion_scores")
        if not isinstance(criteria, list) or not criteria:
            raise BenchmarkAgreementError("every score sheet needs criterion-level scores")
        mapped = {item.get("criterion_id"): item for item in criteria}
        if None in mapped or len(mapped) != len(criteria):
            raise BenchmarkAgreementError("criterion IDs must be present and unique")
        maps.append(mapped)
    criterion_ids = set(maps[0])
    if any(set(mapped) != criterion_ids for mapped in maps[1:]):
        raise BenchmarkAgreementError("all evaluators must score the same criterion IDs")
    criterion_rows = []
    weighted_absolute_differences = Decimal("0")
    total_weight = Decimal("0")
    for criterion_id in sorted(criterion_ids):
        rows = [mapped[criterion_id] for mapped in maps]
        weights = {Decimal(str(item["weight"])) for item in rows}
        if len(weights) != 1:
            raise BenchmarkAgreementError(f"criterion weight mismatch for {criterion_id}")
        weight = weights.pop()
        pair_differences = [
            abs(Decimal(str(left["score"])) - Decimal(str(right["score"])))
            for left, right in combinations(rows, 2)
        ]
        pair_count = len(pair_differences)
        exact = sum(value == 0 for value in pair_differences)
        within_one = sum(value <= 1 for value in pair_differences)
        mean_abs = sum(pair_differences, Decimal("0")) / Decimal(pair_count)
        weighted_absolute_differences += weight * mean_abs
        total_weight += weight
        criterion_rows.append({
            "criterion_id": criterion_id,
            "weight": str(weight),
            "pair_count": pair_count,
            "exact_agreement_rate": str((Decimal(exact) / Decimal(pair_count)).quantize(Decimal("0.0001"))),
            "within_one_point_rate": str((Decimal(within_one) / Decimal(pair_count)).quantize(Decimal("0.0001"))),
            "mean_absolute_score_difference": str(mean_abs.quantize(Decimal("0.0001"))),
        })
    weighted_mean = weighted_absolute_differences / total_weight
    return {
        "report_version": "1.0.0",
        "status": "descriptive_only",
        "binding": {key: first[key] for key in binding},
        "evaluator_count": len(scores),
        "evaluators": sorted(identities),
        "pair_count": len(scores) * (len(scores) - 1) // 2,
        "criterion_results": criterion_rows,
        "weighted_mean_absolute_score_difference": str(weighted_mean.quantize(Decimal("0.0001"))),
        "interpretation": (
            "Descriptive disagreement for these matched score sheets only. "
            "This is not an inter-rater reliability coefficient, does not establish "
            "statistical reliability, and must not be generalized beyond this sample."
        ),
    }
