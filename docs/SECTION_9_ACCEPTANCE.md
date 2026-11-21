# Section 9 Acceptance Register: Operations, Observability, and Free-First Automation

Status: **partially implemented; local/pre-production scope verified; production acceptance not met.**

Repository scope: `Amiroudro/new-sngso` only. This register distinguishes committed code and automated tests from services, permissions, deployment controls, and operational evidence that do not exist.

## Implemented and covered by the full test suite

| Subsection | Implemented capability | Verification boundary |
|---|---|---|
| 9.1 | Baseline operational audit and bounded implementation plan | Repository inspection and documented gaps |
| 9.2 | Read-only health report and bounded pipeline-run listing | Fixture-backed SQLite tests; no live health endpoint |
| 9.3 | Verified local SQLite backup and restore to a new path | Temporary-database integrity, schema, audit-chain and no-overwrite tests |
| 9.4 | Fail-closed retry classification with bounded backoff calculation | Pure policy tests; does not sleep or retry |
| 9.5 | Deterministic idempotent run creation | Duplicate and two-connection race tests |
| 9.6 | Local operator runbook | Documentation only; no on-call service |
| 9.7 | External audit checkpoint export and verification | Local file export and prefix/truncation tests; independent delivery is manual |
| 9.8 | Synchronous injected-stage pipeline runner | Sequential handler tests, terminal history, safe error recording, and two-connection claim race |
| 9.9 | Guarded deliberate retry-run creation | Explicit operator attestations, transient-only policy, immutable input preservation, and attempt-limit tests |
| 9.10 | Immutable retry lineage | Schema and registry enforce parent existence, failed parent status, sequential attempt, unchanged inputs, and immutable lineage |

The latest full Python 3.12 suite passed **893 tests in 9.84 seconds** in GitHub Actions [run #44941362943](https://github.com/Amiroudro/new-sngso/actions/runs/44941362943), on code commit `cad4c823976791f3d9801b1add21bb52e45bf716`.

## Remaining engineering gates

These are not implemented or accepted:

1. Durable per-stage checkpoints and crash reconciliation. The current runner deliberately cannot resume a `running` record.
2. Stage-level idempotency keys and durable external-operation references, including reconciliation after a side effect succeeds but the registry commit fails.
3. Worker lease, heartbeat, timeout, cancellation and process supervision semantics.
4. Authenticated service-worker identities and real handler-level permission checks at point of use.
5. Structured operational event/metric/trace correlation and alert delivery.
6. Automated encrypted backups, key management, off-site storage, retention, and production-like restore drills.
7. Automated delivery of audit checkpoints to an independently controlled account or storage system.
8. A separately authorized scheduler and deployment environment, with operator controls and rollback.

## External acceptance gates

- No real publisher republication grants or authorized production source set are established.
- No genuine independent human editorial review evidence has been supplied.
- No production OIDC issuer/JWKS/revocation service or protected administrator bootstrap is deployed.
- No production database, monitoring service, alert route, or on-call operation exists.
- No Flutter runtime consumes the staging provenance sidecar, and no integrated staging-consumer golden test has been run.
- No live daily pipeline or publication is operating.

## Safety and release decision

- Section 8's edition release path remains staging-only.
- The Section 9 runner accepts injected handlers only; it does not contain publisher transport or publication handlers.
- Retry creation is explicit, requires recorded operator attestations, and creates a queued attempt only. It does not execute the attempt.
- Unknown, permanent, permission, identity, policy, and integrity failures are not retried.
- No merge, production deployment, live collection, schedule activation, external secret configuration, paid service, or publication is authorized by this register.

**Decision:** Section 9 is not complete for production. The local operational foundation is substantially implemented, but end-to-end operational acceptance must wait for the remaining engineering and external gates. The next technical milestone is durable stage checkpointing and safe crash reconciliation, without storing raw sensitive stage outputs by default.
