"""Synchronous, injected-stage pipeline runner.

This module deliberately contains no network transport, scheduler, retry loop,
publisher connector, or publication action. Stage handlers are supplied by the
caller and must make their own side effects idempotent.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable, Sequence

from .canonical import canonical_json_bytes, sha256_fingerprint
from .operations import classify_retry_policy
from .registry import RegistryIntegrityError


class PipelineExecutionError(ValueError):
    """Raised when a pipeline cannot safely be claimed or executed."""


class PipelineStageFailure(RuntimeError):
    """A stage failure with a bounded, caller-supplied safe error classification."""

    def __init__(self, failure_code: str, safe_detail: str) -> None:
        if not isinstance(failure_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", failure_code):
            raise ValueError("failure_code must be a lowercase bounded identifier")
        if not isinstance(safe_detail, str) or not safe_detail.strip() or len(safe_detail) > 2000:
            raise ValueError("safe_detail must contain 1 to 2000 characters")
        self.failure_code = failure_code
        self.safe_detail = safe_detail.strip()
        super().__init__(self.safe_detail)


@dataclass(frozen=True)
class PipelineStage:
    """One named stage and its injected handler."""

    name: str
    handler: Callable[[dict[str, Any]], Any]


def _utc_now(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise PipelineExecutionError("execution time must be timezone-aware")
    return result.astimezone(timezone.utc)


def _validate_stages(stages: Sequence[PipelineStage]) -> tuple[PipelineStage, ...]:
    if not isinstance(stages, (list, tuple)) or not stages:
        raise PipelineExecutionError("at least one explicit pipeline stage is required")
    result = tuple(stages)
    names: set[str] = set()
    for stage in result:
        if not isinstance(stage, PipelineStage):
            raise PipelineExecutionError("stages must be PipelineStage instances")
        if not isinstance(stage.name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", stage.name):
            raise PipelineExecutionError("stage names must be lowercase bounded identifiers")
        if stage.name in names:
            raise PipelineExecutionError("stage names must be unique")
        if not callable(stage.handler):
            raise PipelineExecutionError("every stage must have a callable handler")
        names.add(stage.name)
    return result


def execute_pipeline_run(
    registry: Any,
    pipeline_run_id: str,
    *,
    input_context: dict[str, Any],
    stages: Sequence[PipelineStage],
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Claim one queued run, execute injected stages in order, and persist one outcome.

    A running run is not resumed: stage outputs are intentionally not persisted,
    so resuming could repeat external side effects. A process interruption leaves
    a detectable stale running record for manual investigation.
    """
    if not isinstance(pipeline_run_id, str) or not pipeline_run_id.strip():
        raise PipelineExecutionError("pipeline_run_id is required")
    if not isinstance(input_context, dict):
        raise PipelineExecutionError("input_context must be a JSON object")
    try:
        canonical_json_bytes(input_context)
    except (TypeError, ValueError) as exc:
        raise PipelineExecutionError("input_context must contain finite JSON-compatible values") from exc
    stage_list = _validate_stages(stages)
    if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
        raise PipelineExecutionError("actor and reason are required")
    executed_at = _utc_now(now)

    current = registry.get_pipeline_run(pipeline_run_id)
    if current is None:
        raise PipelineExecutionError("unknown pipeline run")
    record = current["record"]
    if record["status"] != "queued":
        raise PipelineExecutionError("only a queued pipeline run can be claimed")
    input_hash = sha256_fingerprint(input_context)
    if input_hash not in record["input_hashes"]:
        raise PipelineExecutionError("input context fingerprint is not bound to the queued run")

    claimed = dict(record, status="running")
    try:
        claim = registry.transition_pipeline_run(
            pipeline_run_id,
            claimed,
            expected_revision=current["revision"],
            actor=actor,
            reason=f"{reason.strip()} (claim)",
        )
    except RegistryIntegrityError as exc:
        raise PipelineExecutionError("pipeline run was claimed or changed by another worker") from exc

    outputs: dict[str, Any] = {}
    stage_results: list[dict[str, Any]] = []
    failure_code: str | None = None
    failure_detail: str | None = None

    for index, stage in enumerate(stage_list):
        stage_context = {"input": deepcopy(input_context), "outputs": deepcopy(outputs)}
        stage_input_hash = sha256_fingerprint(stage_context)
        try:
            output = stage.handler(stage_context)
            output_hash = sha256_fingerprint(output)
            outputs[stage.name] = output
            stage_results.append({
                "stage": stage.name,
                "status": "succeeded",
                "input_hash": stage_input_hash,
                "output_hash": output_hash,
            })
        except PipelineStageFailure as exc:
            failure_code = exc.failure_code
            failure_detail = exc.safe_detail
            stage_results.append({
                "stage": stage.name,
                "status": "failed",
                "input_hash": stage_input_hash,
                "detail": failure_detail,
            })
        except Exception as exc:
            # Exception messages can contain credentials, URLs, or source text.
            failure_code = "stage_execution_failed"
            failure_detail = f"Stage raised {type(exc).__name__}; sensitive exception text was omitted."
            stage_results.append({
                "stage": stage.name,
                "status": "failed",
                "input_hash": stage_input_hash,
                "detail": failure_detail,
            })

        if failure_code is not None:
            for remaining in stage_list[index + 1:]:
                stage_results.append({
                    "stage": remaining.name,
                    "status": "blocked",
                    "detail": "Not executed because an earlier stage failed.",
                })
            break

    finished_at = _utc_now(None) if now is None else executed_at
    started_at = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
    if finished_at < started_at:
        finished_at = started_at

    if failure_code is None:
        output_hashes = sorted({item["output_hash"] for item in stage_results if "output_hash" in item})
        completed = dict(
            claim["record"],
            status="succeeded",
            finished_at=finished_at.isoformat(),
            output_hashes=output_hashes,
            stage_results=stage_results,
        )
        result = registry.transition_pipeline_run(
            pipeline_run_id,
            completed,
            expected_revision=claim["revision"],
            actor=actor,
            reason=f"{reason.strip()} (completed)",
        )
        return {
            "status": "succeeded",
            "pipeline_run_id": pipeline_run_id,
            "revision": result["revision"],
            "stage_results": stage_results,
            "output_hashes": output_hashes,
            "retry_decision": None,
        }

    retry_decision = classify_retry_policy(failure_code, attempt=1, max_attempts=3)
    failed = dict(
        claim["record"],
        status="failed",
        finished_at=finished_at.isoformat(),
        failure_code=failure_code,
        failure_detail=failure_detail,
        stage_results=stage_results,
    )
    result = registry.transition_pipeline_run(
        pipeline_run_id,
        failed,
        expected_revision=claim["revision"],
        actor=actor,
        reason=f"{reason.strip()} (failed: {failure_code})",
    )
    return {
        "status": "failed",
        "pipeline_run_id": pipeline_run_id,
        "revision": result["revision"],
        "stage_results": stage_results,
        "output_hashes": [],
        "retry_decision": retry_decision,
    }
