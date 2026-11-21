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
