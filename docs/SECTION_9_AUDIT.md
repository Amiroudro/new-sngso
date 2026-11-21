# Section 9: Operations, Observability and Free-First Automation

Status: baseline audit complete; first read-only operational reporting slice implemented. No scheduler or production operations are active.

## 9.1 Repository baseline audit

The Python engine currently has one GitHub Actions workflow for contract validation and the complete pytest suite. It does not have a scheduled production pipeline, deployment workflow, release workflow, protected environment promotion, health endpoint, metrics exporter, trace correlation, alert routing, operator dashboard, backup/restore utility, or operational runbook.

SQLite schema version 31 persists pipeline-run records and revisions. File-backed registries use WAL and a 10-second busy timeout. The audit ledger is hash-linked and exposes a checkpoint, but no external checkpoint publisher exists. The collection module is permission-first orchestration with injected connectors; it does not implement network transport. OIDC verification code exists, but no production issuer/JWKS/revocation infrastructure or protected administrator bootstrap is configured.

The repository therefore has useful durable records and CI, but no running orchestration or production observability layer. A green test suite does not establish that a daily pipeline is operating.

## 9.2 Read-only operational health report

Implemented:

- `SQLiteRegistry.list_pipeline_runs(status=None, limit=100)` provides bounded, newest-first access to validated pipeline-run records.
- `PipelineRunRepository` exposes that read interface through the repository port.
- `build_operational_health_report(...)` reports pipeline-run counts, latest start time, stale queued/running records, recent failed/rejected records, audit-chain validity, and the current audit checkpoint.
- Report output is bounded and deliberately excludes failure_detail, credentials, source content, and full pipeline payloads.
- Status is `not_observed` when no runs exist, `degraded` when audit verification fails or stale/recent failed work is present, and `healthy` only when observed runs have no detected issue. Every report keeps `production_ready: false`.
- Tests cover bounded listing, status filtering, ordering, stale-run detection, recent failures, empty history, invalid arguments, and failure-detail non-disclosure.

Full Python 3.12 suite passed in GitHub Actions [run #44938730003](https://github.com/Amiroudro/new-sngso/actions/runs/44938730003): **839 passed in 11.54 seconds** on commit `9092d8137474dfb119af893d570f38c661201db4`.

This is a read-only report function, not an HTTP health endpoint, monitoring service, alert, scheduler, or operator dashboard. It does not run jobs or mutate pipeline state.

## Remaining Section 9 work

1. Define and test retry classification and idempotency at a future orchestration boundary. Permanent denials and contract/identity failures must not be retried.
2. Implement local SQLite backup and restore utilities with integrity and audit-chain verification, then automated temporary-database restore tests.
3. Write operational runbooks for failed/stuck runs, rights revocation, database lock/corruption, restore, and last-known-good staging recovery.
4. Define safe external audit checkpoint handling and alert thresholds without adding a paid monitoring dependency.
5. Add free-first CI checks for operational utilities. Do not schedule real collection until source permissions, identity infrastructure, and the human approval/release process are established.

## Safety boundary

No scheduled collection, live source access, automatic release, production deployment, external secret configuration, paid service, or live edition publication is authorized by this work. Section 8 remains staging-only.

## 9.3 Verified local SQLite backup and restore

Implemented free-first local backup/restore helpers using Python's built-in SQLite online backup API:

- A backup is written to a temporary file in the destination directory, then checked with SQLite integrity_check, schema-version matching, contiguous audit sequence and hash-chain verification before atomic non-overwriting installation.
- Restore accepts only a verified backup matching the current engine schema version and writes to a new destination. Existing destinations are never overwritten.
- Backup files are created with owner-only permissions (0600) where supported.
- The utilities refuse to start a backup inside an active registry transaction and return the external audit checkpoint value for an operator to store separately.
- Tests cover consistent backup/restore of pipeline-run and audit state, refusal to overwrite, active-transaction rejection, and tampered-audit backup/restore rejection.

GitHub Actions [run #44939132297](https://github.com/Amiroudro/new-sngso/actions/runs/44939132297) passed **842 tests in 8.91 seconds** on commit `120a07f2f822e84f9863651f0153a219ec3116fa`.

This is a local utility, not a scheduled or off-site backup service. Encryption, retention, independent storage, recovery-point objectives, and an actual restore drill against a production-like database remain unimplemented.

## 9.4 Deterministic retry classification

Added a pure retry-policy classifier. It recognizes a narrow set of transient network/HTTP/SQLite-busy failure codes, applies bounded exponential backoff, and stops at the configured attempt limit. Permission denial/expiry/revocation, identity/authentication failure, contract/schema failure, unsupported operation, inactive source/endpoint, content-policy block, and integrity failure are permanent. Unknown codes fail closed and are not retried.

The classifier does not perform retries, sleep, schedule jobs, or mutate pipeline state. It is policy input for a future orchestrator. Tests cover transient retries, backoff caps, attempt exhaustion, permanent and unknown failures, and invalid inputs.

GitHub Actions [run #44939277125](https://github.com/Amiroudro/new-sngso/actions/runs/44939277125) passed **859 tests in 11.54 seconds** on commit `9dad74539a50cb6a548dd09932781013a71c4371`.

### Updated remaining work

- Idempotency and orchestration semantics must be defined before an actual retry loop is added.
- Operational runbooks and incident response remain outstanding.
- Backup encryption, retention, off-site copies, external audit checkpoint storage and a production-like restore drill remain outstanding.
- No scheduler, monitoring service, alert router, live source connector or production deployment exists.

## 9.5 Deterministic idempotent pipeline-run creation

Added a deterministic pipeline-run identity derived from run kind, software version, configuration fingerprint, ordered input fingerprints, initiator, and explicit attempt number. Duplicate submissions for the same logical attempt reuse the existing durable run record. Concurrent submissions from two independent SQLite connections converge on one record. A deliberate retry attempt gets a different identity; changed inputs also produce a different identity. Caller-supplied mismatched IDs are rejected.

This helper only creates or reuses the durable run record. It does not launch workers, schedule execution, mutate terminal runs, or implement a retry loop. Input order is preserved in the idempotency identity because order may be semantically meaningful.

Tests cover repeat submission, changed inputs, new retry attempt, ordered-input identity, malformed input, and a two-connection concurrent submission race. GitHub Actions [run #44939558764](https://github.com/Amiroudro/new-sngso/actions/runs/44939558764) passed **869 tests in 6.65 seconds** on commit `e2728fa5f3df0c97999398815862535fc1b3c826`.

### Section 9 status after 9.5

Implemented so far: read-only operational health reporting, bounded pipeline-run listing, verified local SQLite backup/restore, fail-closed retry classification, and deterministic idempotent run creation.

Still open: a real orchestration/worker boundary, idempotency integration with actual job execution, operational runbooks, external audit checkpoint storage, backup encryption/retention/off-site storage, production restore drills, monitoring/alert routing, and any authorized scheduled pipeline.

## 9.6 Initial local operations runbook

Added docs/OPERATIONS_RUNBOOK.md covering read-only health interpretation, verified local backup, backup verification, restore to a new path, fail-closed retry classification, idempotent run submission, audit-chain limitations, and stop/escalation conditions.

The runbook explicitly states that backup files are not encrypted, external checkpoints are not automatically stored, there is no scheduler/worker/monitoring/alert service, and no production restore drill has been performed. It is initial local guidance, not a production on-call procedure.

## 9.7 External audit checkpoint export and verification

Added helpers to write the current verified audit-chain tip to a new owner-only JSON checkpoint file and verify that a later registry still contains that exact sequence/hash prefix. Verification accepts later appended events, but fails closed if the chain is invalid, truncated before the checkpoint, or has a different hash at the checkpoint sequence. Existing checkpoint files are never overwritten.

The file is an external anchor only if an operator copies it to a separately controlled location. The repository does not automatically upload or store it outside the database host.

Tests cover empty-chain refusal, no-overwrite safety, successful verification after later appends, malformed checkpoint rejection, and detection of audit-chain truncation after bypassing local append-only triggers.

GitHub Actions [run #44939874419](https://github.com/Amiroudro/new-sngso/actions/runs/44939874419) passed **871 tests in 11.11 seconds** on commit `751352dc92c7bdb90f0f3b8d8866d362b3c34db2`.

External checkpoint delivery, monitoring and alerting remain manual/unimplemented.

## 9.8 Synchronous injected-stage pipeline runner

Added `src/sngso_engine/orchestrator.py` with a narrow execution boundary:

- Claims only a queued run, binding the exact JSON input-context fingerprint to the run's immutable input hashes.
- Uses optimistic revision transition to claim the run; a second worker cannot claim or execute the same queued revision.
- Executes explicitly injected stage handlers sequentially, passing copies of the initial input and prior successful outputs.
- Records each stage's input/output fingerprints and succeeded/failed/blocked status in the terminal pipeline-run revision.
- Stops at the first failure and marks later stages blocked.
- Omits unexpected exception messages and raw stage outputs from durable run history and the returned execution result.
- Classifies a known transient failure but does not retry it. No automatic retry can repeat an external side effect.
- Adds no network transport, publisher connector, queue, scheduler, worker pool, lease, heartbeat, resume, approval, staging promotion, or publication action.

A running record is deliberately not resumed because intermediate outputs are not checkpointed. If a process stops after an external side effect but before the final registry transition, the run remains running and requires operator reconciliation. Any future handler with external effects must implement durable idempotency and current permission checks itself.

Tests cover ordered stage handoff, successful terminal history, fail-closed stage stopping, blocked later stages, permanent and transient failure classification without retry, exception-message non-disclosure, preflight rejection, no re-execution of terminal runs, and a two-connection worker claim race.

GitHub Actions [run #44940389931](https://github.com/Amiroudro/new-sngso/actions/runs/44940389931) passed **881 tests in 10.71 seconds** on code commit `c56f745e39fe419d64176038d426d6654e2d5110`.

### Updated Section 9 position

The local/pre-production execution seam now exists, but Section 9 is not complete. Still required: durable stage checkpoints and crash reconciliation, worker leases/heartbeats, cancellation and supervision, real handler-level permission checks and idempotency, operational alerting, encrypted/off-site backups, retention, production-like restore drills, and external checkpoint delivery. No live source handler, scheduled collection, or publication is enabled.

## 9.9 Guarded deliberate retry-run creation

Added `create_pipeline_retry_run` for an operator-approved new attempt after a failed run. It requires the previous attempt number to match the failed run's deterministic ID, preserves the exact configuration and ordered input hashes, and rejects permanent or unknown failure codes and exhausted attempt limits. The new record is queued and has a distinct deterministic ID.

Before creation, the operator must explicitly attest that prior side effects have been reconciled and permissions have been rechecked. The attestation is recorded in the run-creation audit reason. It is not cryptographic identity proof and does not replace a fresh permission check inside every handler. The helper never executes the retry.

Tests cover transient retry creation, duplicate request reuse, immutable input preservation, permanent/unknown failure rejection, attempt identity mismatch, non-failed run rejection, and missing safety attestations.

GitHub Actions [run #44941002132](https://github.com/Amiroudro/new-sngso/actions/runs/44941002132) passed **888 tests in 12.34 seconds** on code commit `aed9cb26ef85334f330e3600f05c6c55eb8f218e`.

### Updated Section 9 position after 9.9

Implemented: operational health reporting, local verified backup/restore, bounded retry classification, deterministic idempotent run creation, external audit checkpoint export/verification, a local operations runbook, a synchronous injected-stage runner, and guarded deliberate retry-run creation.

Still open: actual production identity/authorization, live source-specific handlers and permission verification, durable stage checkpoints and crash reconciliation, worker leases/heartbeats/supervision/cancellation, automated alert delivery, backup encryption/off-site storage/retention, production-like restore drills, external checkpoint delivery, and authorized scheduling. No live collection or publication is enabled.

## 9.10 Immutable retry lineage

The pipeline-run contract now supports optional `attempt` and `retry_of` fields. Explicit retry creation writes both fields into the new queued run. The registry checks the parent exists and is failed, the child attempt immediately follows the parent's attempt, and software version/configuration/input hashes/initiator are unchanged. Run transitions cannot rewrite attempt or parentage.

Legacy attempt-one records without the new fields remain compatible with idempotent reuse. This avoids forcing a destructive rewrite of existing append-only run history.

Tests cover valid parentage, missing and non-failed parents, mismatched attempt number, changed immutable inputs, immutable lineage during transitions, and legacy record reuse.

GitHub Actions [run #44941362943](https://github.com/Amiroudro/new-sngso/actions/runs/44941362943) passed **893 tests in 9.84 seconds** on code commit `cad4c823976791f3d9801b1add21bb52e45bf716`.
