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

| 9.11 | Durable stage checkpoints and crash reconciliation | Local implementation and full-suite CI verified: schema v32; metadata-only immutable checkpoint heads/revisions; deterministic idempotency keys; ordered checkpoint-aware pipeline runner; fail-closed provider reconciliation including later resolution of indeterminate outcomes; point-of-use permission callback; atomic audit transitions; SQLite privacy and identity guards; crash-window, local-commit-failure, rollback, concurrency, stage-order, and result-reconstruction tests. Full Python 3.12 suite: **916 passed in 8.96 seconds** in GitHub Actions [run #45014748421](https://github.com/Amiroudro/new-sngso/actions/runs/45014748421), on commit `282f88182ca018d3893c2d248b8d4a5ece6ff9d6`. Draft PR #8; provider-specific production adapters and production acceptance remain outstanding. |
| 9.12 | Worker leases, heartbeat, and cooperative cancellation | Local implementation and full-suite CI verified: schema v33; one-time bearer tokens stored only as SHA-256 fingerprints; monotonic fencing generations; transactional append-only lease revisions and audit events; bounded heartbeat expiry; stale-owner rejection; takeover after expiry/release; immutable cancellation requests with point-of-use authorization; cooperative worker acknowledgement; optional managed heartbeat session; checkpoint-runner lease guards before/after handlers and before terminal transitions. Full Python 3.12 suite: **934 passed in 13.93 seconds** in GitHub Actions [run #45017991562](https://github.com/Amiroudro/new-sngso/actions/runs/45017991562), on code commit `7144226f6d0b17db8f76b15056febe081c5c96db`. External process supervision, provider-enforced fencing, authenticated production identity, distributed lease durability/HA, and production acceptance remain outstanding. See [Section 9.12 contract](SECTION_9_12_WORKER_LEASES.md). |
| 9.13 | External worker supervision and bounded stage execution | **Partial local implementation; full-suite CI verified.** POSIX-only subprocess supervision now integrates a started `WorkerLeaseSession`: pre-launch lease validation, protected inherited-FD handoff of the lease token/run identity/fencing generation, parent-side heartbeat-loss monitoring, durable cancellation propagation over a separate pipe, and explicit child cancellation acknowledgement. Unacknowledged cancellation and lease loss are distinct failed outcomes; supervisor does not mutate checkpoint state or acknowledge cancellation on behalf of the worker. Added checkpoint-handler adaptation: only validated successful child output can succeed a checkpoint; timeout leaves a side-effecting checkpoint indeterminate, and a stale worker cannot mutate checkpoint state after lease loss. Tests also cover heartbeat renewal, cancellation acknowledgement/unacknowledged cancellation, lease-loss termination, secret isolation, and IPC cleanup. Current code-head CI: **963 passed in 13.77 seconds** [run #45024945093](https://github.com/Amiroudro/new-sngso/actions/runs/45024945093). Checkpoint reconciliation after child termination, provider-enforced fencing/idempotency, OS resource quotas, service-manager/restart recovery, and production operations remain open. See [design](SECTION_9_13_WORKER_SUPERVISION_DESIGN.md) and [implementation note](SECTION_9_13_WORKER_SUPERVISION_IMPLEMENTATION.md). |

## Remaining engineering gates

These are not implemented or accepted:

1. Provider-specific idempotency/reconciliation adapters and durable result reconstruction. The engine now supplies stable keys and fail-closed reconciliation contracts, but no live provider adapter is approved.
2. Complete Section 9.13 acceptance: connect child terminal outcomes to checkpoint reconciliation without replaying uncertain work; add signal/shutdown handling, OS resource limits, distributed lease durability/HA, and provider-enforced fencing/idempotency. The current subprocess primitive is POSIX-only and local; lease generations are coordination metadata, not proof that an expired process has stopped.
3. Authenticated service-worker identities and production permission adapters. The checkpoint runner requires a point-of-use permission callback, but trusted identity and real permission evidence remain external infrastructure.
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

**Decision:** Section 9 is not complete for production. The local operational foundation is substantially implemented, while Section 9.13 has partial local subprocess and protected-IPC primitives but lacks end-to-end lease/checkpoint integration and operational controls. End-to-end operational acceptance must wait for the remaining engineering and external gates. No production worker, scheduler, live collection, publication, release, or deployment is authorized.
