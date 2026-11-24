# Section 9 Final Acceptance and Closure Plan

Status: **engineering foundation substantially implemented; Section 9 is not closed or production-accepted.**

Repository: `Amiroudro/new-sngso`  
Working branch: `feat/section-9-durable-stage-checkpoints`  
Scope: Section 9 operational foundation only. This plan does not authorize a merge, production deployment, live collection, scheduling, publication, or release.

## 1. Closure decision

Section 9 must not be marked complete merely because the local Python suite passes. Its acceptance criteria span four distinct evidence classes:

1. **Repository implementation**: code, schema, migrations, documentation, and automated tests are committed.
2. **Backend and provider conformance**: real independent connections/processes and real side-effect providers prove atomicity, fencing, idempotency, and reconciliation.
3. **Deployment controls**: trusted identity, authorization, service-manager lifecycle, resource isolation, monitoring, backups, and recovery are configured and tested in the target environment.
4. **Operational authorization**: source permissions, human editorial review, operator controls, and release authorization are independently evidenced.

A test fixture, interface protocol, runbook, or design document is not evidence that an external system has been deployed or accepted.

## 2. Current evidence baseline

The current acceptance register records local implementation across Sections 9.1–9.13, including run lifecycle, retries, immutable lineage, checkpoints, worker leases, subprocess supervision, recovery, provider-boundary contracts, and bounded in-process telemetry.

The latest verified full-suite result recorded in that register is **1,015 passing tests in 18.95 seconds**, on code commit `0bda51c43d4c3bee8e2c919fc9b6e86913159802`. That result predates later documentation-only commits. It must not be represented as a fresh run for the current branch head.

The schema inventory records 33 SQLite migrations, 47 table definitions, 25 index definitions, and 131 trigger definitions. This is an extraction inventory, not a PostgreSQL port or proof that every trigger semantic has been ported.

## 3. Non-negotiable invariants

The following properties must remain true through every implementation and migration step:

- A run has one authoritative lifecycle and immutable input identity.
- Retry creation is explicit, bounded, and lineage-preserving; creating a retry does not execute it.
- Unknown, permanent, permission, identity, policy, and integrity outcomes are never automatically retried.
- Checkpoint idempotency keys remain deterministic for the same run/stage identity.
- An uncertain provider outcome remains unresolved until provider evidence authorizes a terminal result or retry.
- A previously persisted provider operation reference cannot be replaced by a different or malformed reference.
- A stale lease holder cannot mutate authoritative checkpoint, run, or lease state.
- Fencing generations are enforced at the provider's own atomic serialization boundary; local checks alone are insufficient.
- Cancellation is cooperative and explicitly acknowledged; process termination is not proof of remote rollback.
- Audit events remain append-only and cryptographically verifiable.
- Sensitive stage content, bearer tokens, and raw worker diagnostics are not persisted in checkpoint or telemetry metadata.
- Publication and release paths remain outside the authority of this operational runner.

## 4. Repository engineering closure gates

| Gate | Required evidence | Current disposition |
|---|---|---|
| 4.1 Local run/registry behavior | Full suite, migration tests, transaction and two-connection race tests | Substantially implemented; preserve latest CI evidence by exact commit |
| 4.2 Checkpoint recovery | Crash-window tests, provider pending/completed/not-started/unknown cases, context fingerprint verification | Substantially implemented; production adapters remain external |
| 4.3 Worker lease correctness | Independent-connection contention, takeover, fencing generation, cancellation and audit atomicity | SQLite local-file behavior tested; same-host scope only |
| 4.4 Process supervision | POSIX child lifecycle, bounded output/deadline, process-group cleanup, secret isolation, cancellation IPC | Local primitive implemented; deployment integration remains |
| 4.5 Recovery authorization | Verified identity bound to worker and run, fail-closed policy | Adapter implemented; trusted request context and real RBAC wiring remain |
| 4.6 Observability | Bounded labels, privacy constraints, concurrency tests | In-process recorder implemented; remote export and alerting remain |
| 4.7 Storage portability | Full-registry backend implementation and real-backend conformance suite | PostgreSQL-native migrations 0001–0024, transaction/audit primitives, edition staging, cursor-bound read/quality ports, and 78-test PostgreSQL integration evidence are implemented in draft PR #8. Migration 0024 installs DELETE guards for 27 append-only tables previously missing PostgreSQL trigger equivalents. Still not accepted: schema-v36 data import/reconciliation, full trigger-by-trigger adversarial coverage under the deployed runtime role, role provisioning, independent process/topology conformance, backup/restore/cutover rehearsal, and HA/failover evidence. |
| 4.8 Operational runbooks | Reviewed startup, readiness, drain, shutdown, recovery, restore and incident procedures | Runbook guidance exists; target deployment validation remains |

A gate may be marked complete only when its evidence is linked to a commit, CI run, test report, or deployment record. A prose assertion is not sufficient.

## 5. PostgreSQL migration: implementation sequence

The current registry's global audit chain spans ordinary source, permission, editorial, benchmark, staging, and operational writes. Pipeline-run, checkpoint, lease, and audit transitions share transaction boundaries. A lease-only PostgreSQL service would split atomic operations and is rejected.

The safe implementation sequence is:

### P0. Freeze and characterize the SQLite contract

- Pin a schema-v36 fixture generated by the actual migration runner.
- Capture table definitions, column types, defaults, primary/unique/foreign keys, CHECK constraints, indexes, partial-index predicates, and trigger bodies from `sqlite_master`.
- Run `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and the application's audit-chain verifier.
- Extract canonical JSON and fingerprint golden vectors.
- Build a trigger-by-trigger invariant catalogue with positive and negative direct-SQL tests.

**Exit:** every schema object and invariant is accounted for against a real schema-v36 database, not only regex-derived source text.

**Current repository status (2026-11-24):** PostgreSQL migrations 0001–0024 and the initial cursor-bound repository/read/staging layer now exist on draft PR #8, including the missing append-only DELETE guard slice. This supersedes the earlier design-only status, but does not satisfy the P1 exit criteria below. In particular, the current CI creates isolated empty PostgreSQL schemas; it does not import a verified schema-v36 dataset or prove all 139 SQLite trigger semantics under a deployment runtime role.

### P1. Define the complete PostgreSQL schema and repository boundary

- Implement PostgreSQL-native migrations from a documented baseline.
- Preserve append-only and revision protections with constraints, privileges, and transaction logic.
- Keep canonical JSON serialization in application code; do not rely on PostgreSQL JSONB textual rendering to reproduce existing hashes.
- Define repository interfaces for all registry domains, not only leases.
- Use one transaction manager for domain state, revisions, and audit writes.
- Document connection-pool ownership, transaction scope, statement timeouts, and shutdown.

**Exit:** schema migration applies from an empty database and from a verified imported schema-v36 dataset; all invariant tests pass against PostgreSQL.

### P2. Prove concurrency and ambiguous-commit behavior

- Run independent worker processes against independent PostgreSQL connections.
- Race lease acquisition, heartbeat, release, cancellation, checkpoint transition, retry creation, and audit append.
- Inject serialization failures, deadlocks, connection loss before commit, connection loss after server commit, and primary restart.
- On ambiguous commit, read authoritative state and reconcile using stable operation identity. Never infer rollback from a lost client response.
- Verify audit sequence/hash continuity under concurrent writes.
- Verify fencing generation never regresses across supported failover.

**Exit:** deterministic conformance suite passes repeatedly on the actual PostgreSQL major version and configured topology.

### P3. Controlled data migration and cutover rehearsal

- Quiesce all writers and record the source database identity and schema version.
- Create and verify a consistent backup.
- Import all registry domains, revisions, and audit events in dependency order.
- Preserve identifiers, timestamps, canonical payloads, and existing audit hashes.
- Compare row counts, key sets, foreign-key relationships, immutable revision sequences, canonical fingerprints, and complete audit-chain head.
- Run read-only shadow comparisons before switching the authoritative writer.
- Define and rehearse the point of no return. Do not dual-write.
- Keep SQLite as the authoritative backend until a single cutover decision is explicitly approved.

**Exit:** a restore-and-cutover rehearsal is repeatable, fully reconciled, and independently reviewed.

### P4. Production durability and failover acceptance

- Document synchronous/asynchronous replication and acknowledged-commit RPO.
- Prove old-primary fencing and single-writer behavior.
- Test connection-pool invalidation and reconnect after failover.
- Verify lease expiry and clock assumptions across worker hosts.
- Run encrypted backup, off-site retention, key-recovery, point-in-time restore, and audit-chain verification drills.
- Demonstrate that failover cannot discard an acknowledged fencing generation or audit append under the claimed RPO.

**Exit:** signed deployment evidence and restore/failover test reports exist for the target topology.

## 6. Provider and worker acceptance gates

### 6.1 Real provider enforcement

For each side-effecting provider, supply a concrete adapter that atomically:

- rejects a fencing generation lower than the highest accepted generation for the protected resource;
- binds the idempotency key to a canonical request fingerprint;
- returns the original result for an exact duplicate;
- rejects the same key with a different request fingerprint;
- exposes authoritative operation lookup for pending/unknown reconciliation.

The enforcement must occur at the provider's own serialization boundary. A local wrapper or fake test provider does not satisfy this gate.

**Exit:** provider contract suite passes against a real provider sandbox or an independently operated conformance service with documented equivalent guarantees.

### 6.2 Child terminal outcome mapping

- Map normal exit, validated result, timeout, signal termination, shutdown, cancellation acknowledged, cancellation unacknowledged, protocol error, lease loss, and cleanup failure to explicit outcomes.
- Prove each outcome reaches the checkpoint layer without converting uncertainty into success or safe retry.
- Verify restart recovery reconciles provider state before replay.
- Keep child stdout/stderr out of durable state and user-visible error details unless a reviewed redaction policy explicitly allows a bounded diagnostic.

**Exit:** outcome matrix has a test for every terminal class and every checkpoint transition is justified by provider/lease evidence.

### 6.3 Deployment identity and authorization

- Wire the OIDC authorizer to a trusted server-side request/session context.
- Configure issuer, audience, key rotation, revocation/session validity, and clock-skew policy.
- Implement per-run RBAC for worker role, tenant ownership, run ownership, and operational approval.
- Protect administrator bootstrap and recovery actions.
- Test denial, identity-provider outage, expired credentials, spoofed worker IDs, and policy-service outage.

**Exit:** deployment-owned identity and RBAC tests pass in a production-like environment. The request-context factory must not manufacture credentials from caller-supplied worker or run IDs.

## 7. Deployment and operations gates

| Area | Required implementation and evidence |
|---|---|
| Admission/readiness | Service manager prevents new work during drain; readiness waits for authorized recovery and blocks unresolved unknown provider outcomes |
| Shutdown | SIGTERM/SIGINT integration, bounded drain, child termination, lease expiry/recovery, and outer service-manager kill budget tested together |
| Resource isolation | Per-worker limits plus aggregate cgroup/container CPU, memory, process, and file-descriptor limits tested under load |
| Monitoring | Authenticated remote metrics/events/traces, bounded cardinality, alert routes, retention, and a tested notification path |
| Backup | Encrypted automated backup, independent key custody, off-site copy, retention, restore drills, and audit verification |
| Audit delivery | Independently controlled destination, authenticated delivery, gap detection, and periodic verification |
| Scheduling | Separately authorized scheduler, overlap prevention, bounded concurrency, pause/kill controls, and operator-visible run inventory |
| Incident response | On-call ownership, severity criteria, credential rotation, provider reconciliation, database restore, and audit preservation runbooks |

**Exit:** each control has a named deployment owner, configuration reference, test record, and recovery procedure. Documentation alone does not close the gate.

## 8. External editorial and release gates

These are outside the local engine's ability to manufacture:

- Written republication/collection permissions for the actual production publisher and endpoint set, with scope, expiry, and restrictions.
- Genuine independent human editorial review evidence for the relevant benchmark and publication workflow.
- Production administrator and reviewer identities with independently verifiable authentication.
- A real staging consumer integration test for the provenance sidecar.
- Explicit operator authorization for any production schedule, source access, release, or publication.

No synthetic test record, model-generated review, or repository commit may be represented as genuine human review or publisher permission.

## 9. Required CI matrix before production consideration

| Dimension | Minimum cases |
|---|---|
| Python | Supported runtime versions, including the pinned production version |
| SQLite | Fresh migration, upgrade, backup/restore, integrity and foreign-key checks |
| PostgreSQL | Disposable service, fresh migration, imported v33 fixture, full repository conformance |
| Concurrency | Independent connections and independent processes; contention and stale-owner takeover |
| Failure injection | Crash before/after commit, lost commit response, DB restart, provider timeout, lease expiry, SIGTERM |
| Provider | Stale fencing rejection, exact duplicate, conflicting payload, pending lookup, completed lookup, not-started proof |
| Security | OIDC claim validation, RBAC denial, secret isolation, direct-SQL invariant enforcement under runtime role |
| Recovery | Restore, audit-chain verification, unresolved checkpoint inventory, no unsafe replay |

A passing SQLite suite is necessary but cannot substitute for PostgreSQL, provider, deployment, or external authorization evidence.

## 10. Change-control and release safeguards

- Keep work on the Section 9 feature branch and its existing draft PR until review and explicit merge authorization.
- Do not merge PR #8, close or retarget unrelated PRs, or rewrite shared branch history as part of this closure work.
- Do not alter the website, Android application, published editions, or content feeds.
- Do not enable live collection, scheduled execution, provider credentials, publication, or release.
- Every new backend must be feature-gated and default to disabled until its acceptance evidence is complete.
- Any migration that changes audit semantics requires a separate design decision and acceptance; it cannot be smuggled in as an implementation detail.

## 11. Final closure checklist

Section 9 may be declared **repository-engineering complete** only when all repository-owned gates in Sections 4–7 have implementation, tests, and review evidence, with any external dependencies clearly marked as deployment blockers.

Section 9 may be declared **production accepted** only when all applicable gates in Sections 5–9 have evidence from the actual target database, provider, identity, deployment, monitoring, backup, and authorized editorial/source environment.

Until then, the correct status is:

> **Section 9: substantially implemented local operational foundation; production acceptance outstanding.**

This is a deliberate safety boundary, not a claim that the work is finished.
