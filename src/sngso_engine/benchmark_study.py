"""Versioned calibration-study protocol validation."""
from __future__ import annotations
from typing import Any
from .canonical import sha256_fingerprint
from .contracts import validate_payload

class BenchmarkStudyError(ValueError):
    """Raised when a calibration-study protocol is internally inconsistent."""

def validate_benchmark_study_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate study design, partition isolation, and blind assignment eligibility."""
    validate_payload("editorial-benchmark-study", payload)
    if isinstance(payload.get("study_version"), bool) or not isinstance(payload.get("study_version"), int):
        raise BenchmarkStudyError("study_version must be an integer")
    partitions = payload["partition_membership"]
    names = ("calibration_item_ids", "blind_evaluation_item_ids", "adjudication_item_ids")
    seen: dict[str, str] = {}
    for partition in names:
        for item_id in partitions[partition]:
            if item_id in seen:
                raise BenchmarkStudyError(f"item {item_id!r} leaks across {seen[item_id]} and {partition}")
            seen[item_id] = partition
    reviewers = {row["reviewer_id"]: row for row in payload["reviewer_plan"]}
    if len(reviewers) != len(payload["reviewer_plan"]):
        raise BenchmarkStudyError("reviewer IDs must be unique")
    strata = {(row["language"], row["task_type"]) for row in payload["language_task_strata"]}
    if len(strata) != len(payload["language_task_strata"]):
        raise BenchmarkStudyError("language/task strata must be unique")
    assignments = {}
    for assignment in payload["blind_assignment_plan"]:
        item_id = assignment["item_id"]
        if item_id in assignments:
            raise BenchmarkStudyError("each blind item must have exactly one assignment-plan row")
        if item_id not in partitions["blind_evaluation_item_ids"]:
            raise BenchmarkStudyError("blind assignment references an item outside blind evaluation partition")
        if (assignment["language"], assignment["task_type"]) not in strata:
            raise BenchmarkStudyError("blind assignment language/task is outside declared strata")
        if len(set(assignment["reviewer_ids"])) < 2:
            raise BenchmarkStudyError("each blind item requires at least two distinct reviewers")
        for reviewer_id in assignment["reviewer_ids"]:
            reviewer = reviewers.get(reviewer_id)
            if reviewer is None:
                raise BenchmarkStudyError("blind assignment references an undeclared reviewer")
            if not reviewer["training_completed"] or not reviewer.get("training_record_reference"):
                raise BenchmarkStudyError("blind reviewers must have documented completed training")
            if assignment["language"] not in reviewer["languages"] or assignment["task_type"] not in reviewer["task_types"]:
                raise BenchmarkStudyError("blind reviewer lacks declared language/task qualification")
        assignments[item_id] = assignment
    if set(assignments) != set(partitions["blind_evaluation_item_ids"]):
        raise BenchmarkStudyError("every blind evaluation item must have a complete assignment plan")
    return {
        "valid": True, "status": "protocol_only", "study_id": payload["study_id"],
        "study_version": payload["study_version"], "protocol_fingerprint": sha256_fingerprint(payload),
        "partition_counts": {"calibration": len(partitions["calibration_item_ids"]),
            "blind_evaluation": len(partitions["blind_evaluation_item_ids"]),
            "adjudication": len(partitions["adjudication_item_ids"])},
        "reviewer_count": len(reviewers), "blind_item_count": len(assignments),
        "production_ready": False,
        "interpretation": ("This is a validated study plan only. It does not show that sampling occurred, "
            "reviewers completed training, scores were collected, the sample is adequate, "
            "or reliability has been established.")
    }
