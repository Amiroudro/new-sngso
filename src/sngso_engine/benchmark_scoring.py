"""Criterion-level benchmark scoring with deterministic weighted aggregation.

Scores document evaluator judgments and evidence. They do not establish truth,
reviewer competence, or production approval.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contracts import validate_payload


def validate_benchmark_scoring(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a score sheet and compute its weighted score deterministically."""
    validate_payload("editorial-benchmark-scoring", payload)
    rows = payload["criterion_scores"]
    ids = [row["criterion_id"] for row in rows]
    unique_ids = len(ids) == len(set(ids))
    total_weight = sum((Decimal(str(row["weight"])) for row in rows), Decimal("0"))
    weights_valid = abs(total_weight - Decimal("1")) <= Decimal("0.000001")
    weighted = sum(
        (Decimal(row["score"]) * Decimal(str(row["weight"])) for row in rows),
        Decimal("0"),
    )
    weighted_score = weighted.quantize(Decimal("0.0001"))
    critical_failure = payload.get("critical_failure", False)
    return {
        "valid": unique_ids and weights_valid,
        "eligible_for_comparison": unique_ids and weights_valid and not critical_failure,
        "checks": [
            {"name": "unique_criterion_ids", "passed": unique_ids},
            {"name": "weights_sum_to_one", "passed": weights_valid},
            {"name": "criterion_evidence_present", "passed": all(bool(row["evidence_references"]) for row in rows)},
        ],
        "weighted_score": str(weighted_score),
        "scale_minimum": 0,
        "scale_maximum": 4,
        "critical_failure": critical_failure,
        "scope_note": (
            "A weighted score is a reproducible aggregation of declared judgments, "
            "not an automated measure of editorial truth. Scores are comparable only "
            "when rubric version, task type, item, language, and scoring conditions match."
        ),
    }
