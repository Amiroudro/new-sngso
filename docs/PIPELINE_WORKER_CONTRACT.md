# Pipeline Worker Contract (Section 9.8)

Status: synchronous, injected-stage execution adapter for local/pre-production use. This is not a scheduler, hosted worker service, live collection pipeline, or production orchestrator.

## Purpose

The registry already records immutable pipeline-run identity and revision history. This adapter adds a narrow execution path that claims a queued run, executes a caller-supplied ordered list of stage handlers, and records one terminal result.

The engine supplies no network connector or publisher-specific handler. Every handler is injected by the caller. A handler must independently enforce source permissions and must make any external side effect idempotent before it is used in a real workflow.

## Example

~~~python
from datetime import datetime, timezone

from sngso_engine import (
    PipelineStage,
    SQLiteRegistry,
    create_pipeline_run_idempotently,
    execute_pipeline_run,
    sha256_fingerprint,
)

input_context = {"edition_date": "2026-11-22", "batch_id": "local-fixture"}

with SQLiteRegistry("data/registry.sqlite") as registry:
    queued = create_pipeline_run_idempotently(
        registry,
        {
            "schema_version": "1.0.0",
            "status": "queued",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "software_version": "engine/0.1.0",
            "config_hash": "sha256:" + "a" * 64,
            "input_hashes": [sha256_fingerprint(input_context)],
            "initiated_by": "local-operator",
        },
        run_kind="local-validation",
        actor="local-operator",
        reason="Create a local validation run.",
    )

    result = execute_pipeline_run(
        registry,
        queued["record"]["id"],
        input_context=input_context,
        stages=[
            PipelineStage("validate-input", lambda context: {"valid": True}),
            PipelineStage("prepare-report", lambda context: {"ready": True}),
        ],
        actor="local-worker",
        reason="Execute local validation stages.",
    )
    print(result["status"], result["stage_results"])
~~~

The example uses synthetic in-memory stage handlers and a current UTC timestamp. Do not treat this example as permission to connect a live source.

## Execution contract

1. The runner accepts only a known `queued` run. It verifies the exact input-context fingerprint is included in the immutable run input hashes.
2. It atomically transitions the run from `queued` to `running` using the current registry revision. A competing worker cannot claim the same revision.
3. Stage names must be unique, bounded identifiers. Stages run sequentially in the order supplied.
4. Each handler receives a copy of the original input and outputs from prior successful stages. Its return value must be finite JSON-compatible data.
5. The runner stores only each stage's input/output fingerprint and status, not the raw stage output. Raw outputs remain in memory only long enough to feed the next stage.
6. On the first failure, later stages are marked `blocked`. The run becomes `failed`; no later handler is called.
7. Exception messages from unexpected errors are omitted because they may contain credentials, source text, or private data. A handler can raise `PipelineStageFailure(code, safe_detail)` to provide a bounded, deliberately safe failure classification.
8. A known transient failure receives a retry-policy classification, but the runner does not retry it. Unknown and permanent failures are not retryable.

## Deliberate limitations

- No automatic retry: retrying a stage may repeat an external side effect. Retry requires stage-level idempotency and a separate recovery design.
- No resume of `running` runs: stage outputs are not durably checkpointed. A process interruption leaves a stale running record for operator investigation; rerunning it could duplicate work.
- No worker lease, heartbeat, cancellation API, timeout enforcement, parallel stages, queue, scheduler, or process supervisor.
- No built-in HTTP, publisher, file-ingestion, database-write, edition-approval, staging-promotion, or publication handler.
- No raw stage output is persisted in pipeline history or returned in the execution result.
- A crash after an external side effect but before the final registry transition can leave the run `running`. Handlers must use their own durable idempotency keys and reconciliation before any real side effect is permitted.

## Safety requirements before real handlers are connected

- Verify current source, endpoint, operation, and republication permissions at the point of use.
- Use provider-backed, revocable identities for human and service actors.
- Make every side effect idempotent and record its durable external operation ID.
- Define stage-level checkpoints and safe recovery/reconciliation for interrupted work.
- Add worker lease/heartbeat and cancellation semantics before concurrent deployment.
- Exercise crash injection at every boundary between external side effect and registry commit.
- Keep edition approval, separate release authorization, staging promotion, and publication as distinct guarded workflows.

This runner is an execution seam for local validation and future integration. It does not make the product production-ready and does not authorize scheduled collection or publication.
