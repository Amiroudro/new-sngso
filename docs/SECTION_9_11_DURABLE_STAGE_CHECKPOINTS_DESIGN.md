# Section 9.11: Durable stage checkpoints and crash reconciliation

Status: **local implementation complete and full-suite CI verified; production integration gates remain open.**

Repository scope: `Amiroudro/new-sngso`. This design extends the local SQLite operational boundary only. It does not authorize live collection, publisher transport, scheduling, release, deployment, or publication.

## Problem statement

The synchronous runner in `src/sngso_engine/orchestrator.py` claims a queued run, executes injected handlers, and writes one terminal run revision. If a process exits after a handler performs an external side effect but before the terminal revision is committed, the run remains `running`. Re-executing the run can repeat the side effect. The current refusal to resume a running run is correct and must remain the default until durable stage-level recovery exists.

## Non-negotiable invariants

1. **Intent before effect:** persist a stage execution intent in SQLite before invoking its handler.
2. **No blind replay:** an `executing` checkpoint is ambiguous after a crash. Never infer that the handler did not run.
3. **Stable operation identity:** derive a deterministic idempotency key from the immutable pipeline-run identity, stage ordinal/name, and exact stage-input fingerprint. A retry of the same logical stage must reuse that key; a deliberately new pipeline attempt has a different run identity and therefore a different key.
4. **Provider reconciliation before repeat:** an interrupted side-effecting stage may continue only after a provider adapter resolves the same idempotency key or durable operation reference. If the provider cannot establish the outcome, mark the checkpoint `indeterminate` and stop.
5. **Replay only by contract:** a stage may be recomputed after interruption only when explicitly declared replay-safe and free of external side effects. The default is not replay-safe.
6. **Metadata-only persistence:** persist hashes, bounded stage identifiers, timestamps, status, and an opaque provider operation reference only when the adapter contract declares it non-secret. Never persist raw article text, stage input/output JSON, credentials, request/response bodies, or exception messages in checkpoint rows or audit events.
7. **Atomic local transitions:** checkpoint head, append-only revision, and audit event must commit in one SQLite transaction.
8. **Fail closed:** stale, conflicting, malformed, unverifiable, permission-denied, or unknown provider outcomes never trigger a fresh external operation.
9. **Permission recheck at point of use:** a recovery adapter must re-evaluate current source/operation permission immediately before any operation that could access or mutate an external system. A historical approval is not a current grant.
10. **No publication path:** this work remains an injected-handler execution seam. No publisher transport or publication handler is added.

## Durable record model

Introduce a schema migration after version 31 with two tables:

- `pipeline_stage_checkpoints`: one current checkpoint per `(pipeline_run_id, stage_ordinal)`, with immutable stage identity and input fingerprint, deterministic idempotency key, status, optional bounded provider identifier and opaque operation reference, output fingerprint, revision, timestamps, and canonical metadata-only payload.
- `pipeline_stage_checkpoint_revisions`: append-only revision history containing checkpoint ID, revision, timestamp, actor, reason, and metadata-only payload.

Required database protections:

- foreign key to `pipeline_runs`;
- unique `(pipeline_run_id, stage_ordinal)`;
- unique idempotency key;
- immutable checkpoint/run/stage/input/idempotency identity;
- sequential revision increments;
- explicit legal status transitions;
- payload-to-indexed-column consistency;
- append-only revision history and no checkpoint deletion;
- each new revision must match the current checkpoint head;
- audit-chain event in the same transaction as each state change.

Suggested states:

- `executing`: intent is durable; handler may or may not have started or completed.
- `succeeded`: a verified result fingerprint is recorded; raw result is not stored.
- `failed`: a known, bounded failure classification was recorded.
- `indeterminate`: provider outcome cannot be proven; operator/provider investigation is required.
- `blocked`: a prior stage did not reach a safely usable success state.

A checkpoint's `executing` state is intentionally conservative. It covers both the window after intent persistence but before handler invocation and the dangerous window after an external side effect but before local completion persistence.

## Handler and recovery contract

Add a separate checkpoint-aware execution API rather than silently changing the existing runner's semantics.

A checkpoint-aware stage adapter must declare:

- stable stage name and ordinal;
- whether it is replay-safe (default false);
- provider/operation namespace;
- an execution function receiving the deterministic idempotency key and in-memory stage context;
- for side-effecting work, a reconciliation function that queries the provider by that same key or by a previously persisted opaque operation reference;
- a bounded, non-secret operation reference when the provider supplies one;
- a result reconstruction path for a previously completed operation, so later stages can receive the result without storing its raw contents locally.

The adapter's reconciliation result must be one of:

- `completed`: provider proves the operation completed and can reconstruct the result; verify and record the output fingerprint without repeating the effect.
- `not_started`: provider proves the key has never been accepted; execution may proceed once with the same key, after a fresh permission check.
- `pending`: provider reports an in-progress operation; leave the checkpoint executing and stop this recovery pass.
- `unknown`: provider cannot prove the outcome; transition to indeterminate and stop.
- `denied`: current permission or provider policy denies recovery; transition to blocked/indeterminate according to the explicit policy and do not execute.

A provider that supports neither durable idempotency keys nor lookup by key/reference is not eligible for automatic recovery of side-effecting stages. Human reconciliation may resolve the incident, but must not rewrite history or silently reset an executing checkpoint.

## Crash-window handling

| Crash point | Durable state | Recovery action |
|---|---|---|
| Before intent transaction | No checkpoint | Safe to create the intent |
| After intent, before handler | executing | Reconcile by key; execute only if provider proves not_started, or replay if explicitly replay-safe |
| During external operation | executing | Query provider; pending/unknown never repeats |
| After external success, before local completion | executing | Reconcile by key/reference; reconstruct result and record success |
| After success transaction | succeeded | Reuse verified result reconstruction; do not repeat the effect |
| After known failure transaction | failed | Follow explicit retry policy; do not reset this checkpoint |
| Conflicting or unverifiable provider response | executing/indeterminate | Stop and require investigation |

## Recovery and run-state rules

- A run may be resumed only through the new checkpoint-aware API and only when its ordered stage plan matches the persisted stage identities and input fingerprints exactly.
- Completed stages must be reconstructed from their provider references or deterministic replay-safe computation. A missing reconstruction capability is a hard stop, not permission to rerun.
- The next stage is not started until the prior stage has a durable `succeeded` checkpoint and an in-memory result whose fingerprint matches the stored output fingerprint.
- A failed, blocked, or indeterminate checkpoint prevents later stages from running.
- Terminal pipeline-run records remain terminal. Recovery is not implemented by mutating a failed/succeeded run back to queued/running. Deliberate retries remain new runs with the existing retry-lineage rules.
- Stale `executing` checkpoints are candidates for reconciliation, not proof of worker death. Lease/heartbeat/supervision semantics are a separate later milestone.

## Data minimisation

Checkpoint payloads must be allowlisted, not copied from handler input. The default persisted fields are identifiers, hashes, status, revision, timestamps, provider namespace, and a bounded opaque operation reference. The operation reference must be rejected if it resembles credentials, signed URLs, bearer tokens, or raw request material. Provider adapters own their own secure storage for any data needed to reconstruct results.

Audit events record state changes and hashes only. Do not include exception strings, source URLs, article text, prompt text, or handler result objects.

## Required tests

- migration 31 to the next schema version, including a database with existing pipeline-run history;
- checkpoint identity/idempotency-key determinism and uniqueness;
- atomic checkpoint-head/revision/audit writes and rollback under injected failures;
- append-only and direct-SQL tampering guards;
- two independent SQLite connections racing to create/advance one checkpoint;
- crash injection at every row in the crash-window table;
- provider reconciliation outcomes: completed, not_started, pending, unknown, denied, malformed, and provider timeout;
- external success followed by simulated local commit failure, proving recovery finds the same operation and does not invoke it twice;
- explicit replay-safe stage recovery versus default-deny replay;
- exact stage-plan/input mismatch rejection;
- result reconstruction fingerprint mismatch rejection;
- sensitive-data non-disclosure in checkpoint rows, revision history, audit events, and raised errors;
- permission revocation between initial intent and recovery;
- existing Section 9 regression suite unchanged.

## Acceptance boundary

Section 9.11 is complete only when the schema, registry API, checkpoint-aware runner, recovery adapter contract, crash-injection tests, and full Python 3.12 suite are implemented and CI-verified. Documentation alone is not implementation evidence.

Even after repository-level acceptance, Section 9 remains pre-production until worker identity, leases/supervision, real permission adapters, encrypted/off-site backup, external audit checkpoint delivery, monitoring, and deployment controls are independently established. Section 8 remains staging-only. No live collection, scheduling, release, deployment, or publication is authorized.


## Implementation and verification record

The implementation is in src/sngso_engine/stage_checkpoints.py, with SQLite schema migration 32 in registry.py.

- StageCheckpointStore creates a durable intent before a handler runs and appends immutable revisions and audit-chain events atomically.
- stage_idempotency_key binds the pipeline run, stage ordinal/name, and exact stage-input fingerprint.
- execute_checkpointed_stage supports fail-closed reconciliation. A provider-confirmed completed operation is recorded without replay; a provider-confirmed not-started operation may execute with the same stable key after permission is rechecked. Pending and unknown outcomes do not replay. An indeterminate checkpoint can be reconciled again later.
- execute_checkpointed_pipeline enforces sequential predecessor completion, reconstructs prior successful outputs and verifies their fingerprints, stops at unresolved checkpoints, and only marks the run succeeded after every stage is durably successful.
- SQLite guards restrict checkpoint metadata to bounded opaque tokens, bind revision snapshots to the current head, prevent provider-reference replacement, and reject update/delete tampering.
- The legacy execute_pipeline_run behavior remains unchanged.

Full Python 3.12 regression suite: **916 passed in 8.96 seconds** in GitHub Actions [run #45014748421](https://github.com/Amiroudro/new-sngso/actions/runs/45014748421), on commit 282f88182ca018d3893c2d248b8d4a5ece6ff9d6.

This is local-engine acceptance only. Provider-specific adapters must enforce idempotency and provide durable lookup/reconstruction; worker leases, heartbeat/supervision, authenticated service identity, production permission adapters, external audit delivery, and operational backup/restore controls remain separate gates. No live collection, scheduling, publication, release, or deployment is authorized.
