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
