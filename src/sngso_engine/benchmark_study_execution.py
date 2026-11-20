"""Validation for immutable benchmark study execution evidence."""
from __future__ import annotations
from typing import Any
from .contracts import validate_payload
from .canonical import sha256_fingerprint

class BenchmarkStudyExecutionError(ValueError):
    """Raised when study execution evidence is internally inconsistent."""

def validate_benchmark_study_execution(payload: dict[str, Any]) -> dict[str, Any]:
    validate_payload("editorial-benchmark-study-execution", payload)
    from datetime import datetime
    def parse(value: str, field: str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise BenchmarkStudyExecutionError(f"{field} must be a valid timezone-aware timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise BenchmarkStudyExecutionError(f"{field} must be timezone-aware")
        return parsed
    if parse(payload["completed_at"], "completed_at") < parse(payload["started_at"], "started_at"):
        raise BenchmarkStudyExecutionError("execution completion cannot precede its start")
    if parse(payload["analysis"]["reviewed_at"], "analysis.reviewed_at") < parse(payload["completed_at"], "analysis.reviewed_at"):
        raise BenchmarkStudyExecutionError("methodological review cannot precede execution completion")
    scores = payload["score_records"]
    missing = payload["missing_records"]
    score_ids = [row["score_id"] for row in scores]
    score_pairs = [(row["item_id"], row["evaluator_id"]) for row in scores]
    missing_pairs = [(row["item_id"], row["evaluator_id"]) for row in missing]
    if len(score_ids) != len(set(score_ids)):
        raise BenchmarkStudyExecutionError("score IDs must be unique")
    if len(score_pairs) != len(set(score_pairs)):
        raise BenchmarkStudyExecutionError("scored item/evaluator pairs must be unique")
    if len(missing_pairs) != len(set(missing_pairs)):
        raise BenchmarkStudyExecutionError("missing item/evaluator pairs must be unique")
    if set(score_pairs).intersection(missing_pairs):
        raise BenchmarkStudyExecutionError("an item/evaluator pair cannot be both scored and missing")
    analysis = payload["analysis"]
    if analysis["scored_pair_count"] != len(scores) or analysis["missing_pair_count"] != len(missing):
        raise BenchmarkStudyExecutionError("analysis counts must match the execution evidence")
    if analysis["confidence_interval"]["lower"] > analysis["confidence_interval"]["upper"]:
        raise BenchmarkStudyExecutionError("confidence interval lower bound cannot exceed upper bound")
    return {
        "valid": True,
        "execution_fingerprint": sha256_fingerprint(payload),
        "score_record_count": len(scores),
        "missing_record_count": len(missing),
        "methodologist_id": analysis["methodologist_id"],
        "production_ready": False,
        "interpretation": "This record binds declared execution evidence and a reviewed analysis artifact. It does not independently reproduce the analysis or establish benchmark production suitability."
    }
