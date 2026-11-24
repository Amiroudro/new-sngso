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

Historical full-suite baseline: GitHub Actions [run #1458](https://github.com/Amiroudro/new-sngso/actions/runs/45165458998) passed 1,097 tests on code commit `6a6dbb0ceb7776547c2850c25e7bd8d861ab2422` and generated schema-v35 artifact `14813938141`. The current schema-v36 code is verified separately by run #1480 below.

| 9.11 | Durable stage checkpoints and crash reconciliation | Local implementation and full-suite CI verified: schema v32; metadata-only immutable checkpoint heads/revisions; deterministic idempotency keys; ordered checkpoint-aware pipeline runner; fail-closed provider reconciliation including later resolution of indeterminate outcomes; point-of-use permission callback; atomic audit transitions; SQLite privacy and identity guards; crash-window, local-commit-failure, rollback, concurrency, stage-order, and result-reconstruction tests. Full Python 3.12 suite: **916 passed in 8.96 seconds** in GitHub Actions [run #45014748421](https://github.com/Amiroudro/new-sngso/actions/runs/45014748421), on commit `282f88182ca018d3893c2d248b8d4a5ece6ff9d6`. Draft PR #8; provider-specific production adapters and production acceptance remain outstanding. |
| 9.12 | Worker leases, heartbeat, and cooperative cancellation | Local implementation and full-suite CI verified: schema v33; one-time bearer tokens stored only as SHA-256 fingerprints; monotonic fencing generations; transactional append-only lease revisions and audit events; bounded heartbeat expiry; stale-owner rejection; takeover after expiry/release; immutable cancellation requests with point-of-use authorization; cooperative worker acknowledgement; optional managed heartbeat session; checkpoint-runner lease guards before/after handlers and before terminal transitions. Full Python 3.12 suite: **934 passed in 13.93 seconds** in GitHub Actions [run #45017991562](https://github.com/Amiroudro/new-sngso/actions/runs/45017991562), on code commit `7144226f6d0b17db8f76b15056febe081c5c96db`. External process supervision, provider-enforced fencing, authenticated production identity, and a multi-host shared-database lease backend remain production gates. The public `WorkerLeaseBackend` protocol now defines the substitution contract consumed by managed heartbeat sessions; the SQLite store passes its protocol conformance test and independent-connection contention/takeover tests. A PostgreSQL implementation is now under draft PR #8: native migrations 0001–0023 cover the source/permission/editorial/benchmark domains, pipeline runs/checkpoints/leases, edition artifacts/decisions/release authorization, and staging state; cursor-bound read ports include revision history, story snapshots, learning quality, benchmark review/acceptance quality, readiness, and descriptive evaluator agreement. Atomic PostgreSQL staging and audit operations have dedicated independent-connection tests. This is repository implementation, not a production cutover: no role provisioning, schema-v36 import/restore rehearsal, real multi-host worker/provider conformance, HA/failover, or production authorization has been accepted. See the [PostgreSQL implementation checkpoint](SECTION_9_POSTGRESQL_IMPLEMENTATION_CHECKPOINT_2026-11-23.md) and [PR #8](https://github.com/Amiroudro/new-sngso/pull/8). See the [Section 9.12 contract](SECTION_9_12_WORKER_LEASES.md) and [backend contract](SECTION_9_12_BACKEND_CONTRACT.md). |
| 9.13 | External worker supervision and bounded stage execution | **Partial local implementation; full-suite CI verified.** POSIX-only subprocess supervision now integrates a started `WorkerLeaseSession`: pre-launch lease validation, protected inherited-FD handoff of the lease token/run identity/fencing generation, parent-side heartbeat-loss monitoring, durable cancellation propagation over a separate pipe, and explicit child cancellation acknowledgement. Unacknowledged cancellation and lease loss are distinct failed outcomes; supervisor does not mutate checkpoint state or acknowledge cancellation on behalf of the worker. Added checkpoint-handler adaptation: only validated successful child output can succeed a checkpoint; timeout leaves a side-effecting checkpoint indeterminate, and a stale worker cannot mutate checkpoint state after lease loss. Tests also cover heartbeat renewal, cancellation acknowledgement/unacknowledged cancellation, lease-loss termination, secret isolation, and IPC cleanup. Current full-suite CI: **1,000 passed in 18.59 seconds** [run #45044365867](https://github.com/Amiroudro/new-sngso/actions/runs/45044365867), on tested code head `8ba63792cfa1d02f3cde7091b24ddfba587a0f3f`. Subsequent batches added OIDC recovery authorization, service-manager runbook guidance, bounded local worker telemetry, and independent-connection SQLite lease coordination tests. CI results for those commits are tracked separately. The checkpoint layer now persists a provider operation reference on the first pending reconciliation result and supports later polling without replay. A typed `ProviderCommand`, `FencedProviderAdapter` protocol, and dispatch wrapper carry run/stage identity, idempotency key, request fingerprint, and lease fencing generation to the provider, with local lease checks before and after dispatch. Contract tests verify stale-generation rejection, exact-key deduplication, conflicting-payload rejection, and stale local lease refusal using a fake provider. A checkpoint-compatible handler factory now forwards the checkpoint-generated key and lease generation. A concrete production provider adapter must still implement atomic fencing and deduplication at its own boundary. Reconciliation now fails closed when an adapter returns a malformed operation reference or changes the reference previously persisted for a pending operation. A provider call that completes during lease takeover cannot be committed by the stale worker: the checkpoint remains unresolved for reconciliation. Regression tests cover both reference-integrity cases and the in-flight provider/lease-loss crash window. The local supervisor now exposes an idempotent bounded shutdown method: it stops new launches, terminates active process groups with SIGTERM/SIGKILL escalation under one global budget, and reports a non-success shutdown outcome. A read-only `StageCheckpointStore.list_unresolved()` inventory exposes executing/indeterminate checkpoints and their stable provider reconciliation handles for restart inspection without mutating or replaying them. Regression tests cover active-child shutdown, launch rejection, idempotency, bounded shutdown, and non-mutating recovery inventory. A lease-bound restart-recovery API now authenticates through a required authorization callback, acquires a fresh lease, resolves trusted original stage context, verifies its fingerprint, and delegates provider reconciliation/replay decisions to the checkpoint runner. Tests cover completed, not-started, pending, context-mismatch, authorization-denial, and lease-loss cases. An OIDC-backed recovery authorizer now composes the existing verified identity-provider contract with per-run authorization: it binds the authenticated principal to the worker ID, checks issuer/audience/session validity, and fails closed unless the application RBAC policy returns literal `True`. Tests cover spoofing, invalid claims, provider failure, and policy denial. Still open: deployment-specific trusted request-context factory and per-run RBAC policy wiring, service-manager admission/drain integration, cgroup/container and aggregate resource quotas, distributed lease durability/HA, concrete production provider adapters, and production operations. Per-worker Linux CPU-time, address-space, and open-file hard limits are now available through opt-in `WorkerResourceLimits` and util-linux `prlimit(1)`; configured limits fail closed when the helper or Linux platform is unavailable. The new `OIDCWorkerRecoveryAuthorizer` composes verified identity with per-run authorization; deployment-specific request-context and RBAC callbacks remain required. See [design](SECTION_9_13_WORKER_SUPERVISION_DESIGN.md), [implementation note](SECTION_9_13_WORKER_SUPERVISION_IMPLEMENTATION.md), and the [systemd service-manager runbook](SECTION_9_13_SERVICE_MANAGER_RUNBOOK.md). A bounded in-process WorkerTelemetry recorder now integrates with WorkerSupervisor for fixed-label outcome counters, latency buckets, and a capped recent-outcome ring; tests verify concurrent updates and privacy/cardinality bounds. It is local memory only and is not a remote monitoring backend. See [observability implementation note](SECTION_9_13_OBSERVABILITY.md). |

## P0 live schema inspection implementation

The read-only `inspect_sqlite_schema` utility inspects an existing SQLite database directly. It reports live `sqlite_master` definitions, schema version and contiguous migration history, integrity-check output, foreign-key violations, retained audit-chain validity, optional comparison with an independently held audit checkpoint, object counts, and a deterministic schema digest. It refuses to create a missing database and opens the target with URI `mode=ro`. The command-line usage and limitations are documented in [live SQLite schema inspection](SECTION_9_SCHEMA_INSPECTION.md).

**Schema-v34/v35 implementation:** migration 34 added database-level guards for domain and permission-grant revision insertion, permission-grant legal head transitions, and benchmark assignment revision insertion and legal status transitions. Migration 35 adds automatic domain-record revision snapshots, immutable identity checks, and last-actor/reason/revision-time fields. Direct-SQL regression tests cover forged revisions, grant updates without matching revision, permission-scope rewrites, illegal assignment status jumps, automatic domain revision snapshots, revision jumps, payload/index mismatch, article identity rebinding, identity/creation-time rewrites, timestamp rollback, and direct UPDATE/DELETE attempts against domain-record and permission-grant revision histories. A further direct-SQL test proves story-decision records reject UPDATE and DELETE while preserving the audit chain.

**Historical schema-v35 fixture (superseded by v36 below):** GitHub Actions [run #1458](https://github.com/Amiroudro/new-sngso/actions/runs/45165458998) passed **1,097 tests**, generated a fresh schema-v35 registry, ran the read-only inspector, and uploaded `sqlite-schema-v35-inspection` (artifact ID `14813938141`). The inspection workflow completed successfully against the current code/test head. The previously recorded structural baseline remains 1–35 contiguous migrations, `PRAGMA integrity_check = ok`, zero foreign-key violations, a valid retained audit chain, and **47 tables, 25 explicit indexes, 138 triggers across 40 tables, and 0 views**; this artifact is historical schema-v35 evidence and is superseded by the schema-v36 artifact recorded below.

All **139 of 139** SQLite triggers now have source-level semantic review records across the four linked review documents. This does not close all direct-SQL negative test cases, prove PostgreSQL equivalence, or complete P0. Remaining work includes broader adversarial SQL tests, PostgreSQL implementation/conformance, external audit-checkpoint delivery, and operational acceptance.

## PostgreSQL migration design artifacts

- [Operational PostgreSQL migration architecture](SECTION_9_OPERATIONAL_POSTGRESQL_MIGRATION_DESIGN.md): transaction boundary, schema mapping, locking, audit serialization, migration, cutover, rollback, and CI acceptance design.
- [PostgreSQL schema inventory](SECTION_9_POSTGRESQL_SCHEMA_INVENTORY.md): source-derived inventory of migrations 1–36, 47 tables, 25 indexes, and 139 triggers.
- [Final acceptance and closure plan](SECTION_9_FINAL_ACCEPTANCE_AND_CLOSURE_PLAN.md): non-negotiable invariants, repository engineering gates, PostgreSQL implementation sequence, provider conformance, deployment controls, external editorial gates, CI matrix, and release safeguards.
- [Live SQLite schema inspection](SECTION_9_SCHEMA_INSPECTION.md): read-only live database inventory, integrity and foreign-key checks, audit-chain verification, and deterministic schema digest.
- [Trigger porting ledger](SECTION_9_TRIGGER_PORTING_LEDGER.md): live trigger event distribution, table coverage, high-risk families, and the per-trigger PostgreSQL semantic-review requirements.
- [Complete trigger inventory](SECTION_9_TRIGGER_INVENTORY_COMPLETE.md): all 139 migration-defined trigger SQL bodies with mechanical event, predicate, and reference extraction.
- [Operational trigger semantic review](SECTION_9_OPERATIONAL_TRIGGER_SEMANTIC_REVIEW.md): source-level review of all 23 pipeline-run, checkpoint, and worker-lease triggers, with PostgreSQL translation hazards and explicit remaining direct-SQL test gaps.
- [Publication, staging, and audit trigger review](SECTION_9_PUBLICATION_AUDIT_TRIGGER_REVIEW.md): source-level review of 27 edition/publication, audit, story-decision, and timeline-event triggers, with current test evidence and PostgreSQL migration hazards.
- [Benchmark governance trigger review](SECTION_9_BENCHMARK_GOVERNANCE_TRIGGER_REVIEW.md): source-level review of all 39 benchmark-governance triggers, including schema-v35 assignment revision and status-transition guards.
- [Content, provenance, and ingestion trigger review](SECTION_9_CONTENT_PROVENANCE_TRIGGER_REVIEW.md): source-level review of 49 content/provenance/ingestion triggers, including schema-v35 domain and permission-grant revision guards. Together, the four review documents cover all 139 SQLite triggers.

The PostgreSQL documents remain design and inventory artifacts only. The live SQLite inspector, schema-v36 migration, and current direct-SQL regression tests passed in CI. All 139 current SQLite triggers have source-level semantic review records. Direct-SQL conformance gaps remain, and no PostgreSQL implementation or multi-host acceptance has been achieved.

## PostgreSQL repository implementation status

Draft PR #8 contains a PostgreSQL-native registry foundation through migration
0022. It includes source/permission, collection/ingestion, story/editorial,
learning/glossary, benchmark governance, pipeline run/checkpoint/lease,
edition artifact/decision/release authorization, and atomic staging schemas.
The read adapter now has cursor-bound history, source-rights evaluation,
story snapshot and learning-quality parity, and benchmark review/acceptance
quality reports. The benchmark story-snapshot path now includes review
revision history and acceptance quality reports rather than failing solely
because linked benchmark evidence exists.

The latest verified backend test checkpoint is [CI run
#45241428950](https://github.com/Amiroudro/new-sngso/actions/runs/45241428950):
**69 PostgreSQL integration tests passed in 17.63 seconds**, and contract
validation passed. This includes exact SQLite/PostgreSQL benchmark-linked
story snapshot comparison, live source-permission rechecking and locking,
exact study protocol/execution acceptance evidence, the full two-distinct-
attested-human-review positive path, and draft plus approved-supported-summary learning-object quality report
parity against SQLite, plus independent Bangla/English variant review states, duplicate-language rejection, exact SQLite/PostgreSQL independent bilingual-verification parity, MCQ distractor-rationale quality report parity, and draft plus approved vocabulary/glossary/source-evidence quality report parity. The verifier documentation update also
passed [CI run #45235298683](https://github.com/Amiroudro/new-sngso/actions/runs/45235298683).
PR #8 remains draft and unmerged.

This does not satisfy the PostgreSQL migration acceptance exit criteria.
There is no least-privilege deployment-role provisioning and credential
handoff, no import of a verified schema-v36 database, no full data/audit
reconciliation or restore/cutover rehearsal, and no target-topology failover
or multi-host worker/provider conformance evidence. Those are explicit
remaining engineering/deployment gates, not implied by the passing
integration tests.

## Remaining engineering gates

These are not implemented or accepted:

- A PostgreSQL lease-only adapter is not sufficient for multi-host acceptance. The global audit chain is shared by operational and non-operational registry writes, so preserving its current semantics requires moving the complete registry to PostgreSQL in one transaction boundary, or explicitly redesigning and re-accepting the audit model. Until then, retain SQLite and its same-host coordination limit. See the [backend transaction-boundary decision](SECTION_9_12_BACKEND_CONTRACT.md) and [full PostgreSQL migration architecture](SECTION_9_OPERATIONAL_POSTGRESQL_MIGRATION_DESIGN.md).
- Concrete provider-specific idempotency/reconciliation adapters and durable result reconstruction. The engine now supplies stable keys, persists provider operation references, and transports fencing generations through a typed provider contract. No production provider adapter has yet demonstrated atomic fencing, idempotency, or reconciliation.
- Complete Section 9.13 acceptance: connect all child terminal outcomes to checkpoint reconciliation without replaying uncertain work; integrate the signal adapter with a service manager, wire the OIDC recovery authorizer to the deployment's trusted request-context factory and per-run RBAC policy, cgroup/container and aggregate resource limits, distributed lease durability/HA, and a real provider adapter that enforces fencing and idempotency atomically. The current subprocess primitive is POSIX-only and local; lease generations are coordination metadata, not proof that an expired process has stopped.
- Authenticated service-worker identities and production permission adapters. The checkpoint runner requires a point-of-use permission callback, but trusted identity and real permission evidence remain external infrastructure.
- Remote structured event/metric/trace export, authenticated scrape or push delivery, alert routing, and trace correlation. The local bounded WorkerTelemetry recorder is not a substitute for these services.
- Automated encrypted backups, key management, off-site storage, retention, and production-like restore drills.
- Automated delivery of audit checkpoints to an independently controlled account or storage system.
- A separately authorized scheduler and deployment environment, with operator controls and rollback.

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

**Decision:** Section 9 is not complete for production. The local operational foundation is substantially implemented, while Section 9.13 has local subprocess, lease/checkpoint integration, bounded shutdown, and read-only recovery-inspection primitives, but lacks production provider enforcement and external operational controls. End-to-end operational acceptance must wait for the remaining engineering and external gates. No production worker, scheduler, live collection, publication, release, or deployment is authorized.


## Latest staging transaction-boundary verification

GitHub Actions run [#1463](https://github.com/Amiroudro/new-sngso/actions/runs/45166055307) passed **1,099 tests in 21.51 seconds** on code commit `d49373872c7d22c1ec49758d38edec315f6603c0`. The run generated the schema-v35 inspection artifact (ID `14814905301`).

Two fixture-backed direct-SQL tests now cover the valid staging event/state lifecycle: one commits a fully authorized promotion event and its matching channel-state pointer in a single transaction; the other injects an invalid state revision after the event insert and verifies that both writes roll back. This closes the narrow SQLite database-transaction test gap for the first staging promotion. It does not provide a staging application service, rollback-operation lifecycle coverage, concurrent independent-connection race coverage, or PostgreSQL equivalence. Those remain open acceptance items.


## Latest Section 9 verification: staging lifecycle and SQL boundary

GitHub Actions [run #1467](https://github.com/Amiroudro/new-sngso/actions/runs/45172898812) passed the complete Python 3.12 suite: **1,118 passed in 20.58 seconds** on code commit `0268b5455ababb4f60585172bd5a84ed4c99f230`. The live schema-v35 inspection completed and uploaded artifact `14817407520`.

The staging SQL acceptance batch now includes:

- valid promotion event and matching channel-state pointer committed in one transaction;
- injected invalid state advancement proving the preceding event insert rolls back;
- two promotions followed by rollback to the earlier promoted export, with current and last-known-good event/export pointers checked;
- independent SQLite connections racing the same state revision, with exactly one accepted writer;
- missing required JSON keys and wrong scalar types in event payload and export package;
- forged channel-state revision and current/last-known-good pointers rejected by the state guard.

The current branch therefore has local SQLite evidence for staging transaction atomicity, rollback semantics, revision serialization, and the listed direct-SQL rejection cases. It still does not have a public application-level staging service, a complete rollback-operation API, PostgreSQL schema/repository implementation, PostgreSQL role/privilege tests, or production authorization freshness. The PostgreSQL migration design now records the staging lock, authorization, rollback-pointer, and conformance requirements in Section 13.

The run above tests code commit `0268b5455ababb4f60585172bd5a84ed4c99f230`. Later documentation-only commits update this acceptance record and the PostgreSQL design; they do not change the tested code.


## Schema-v36 staging authority identity correction

Migration 36 closes a staging authorization attribution gap. The event trigger now binds the approver and release-authorizer identities declared in the event payload to the principals stored in the referenced immutable decision and release-authorization rows. The relational guard also binds both records to the event manifest and requires the authorization to reference the exact decision.

Added direct-SQL adversarial tests for both identity-rebinding paths using alternate active human principals with the required roles, plus a v35-to-v36 migration test. The schema-inspection assertion now expects 47 tables, 25 explicit indexes, 139 triggers, and zero views. These changes are verified by GitHub Actions run #1480 and live schema artifact 14818506827.

The change does not implement PostgreSQL equivalence, authenticated-session freshness, or a higher-level staging service. PR #8 remains draft and unmerged.


### Verified schema-v36 authority-binding batch

GitHub Actions [run #1480](https://github.com/Amiroudro/new-sngso/actions/runs/45174698086) passed the full Python 3.12 suite: **1,122 passed in 26.28 seconds**. Tested code commit: `d4924a1e1f0e2776923db1bfcc3907998ea85f0b`. The workflow generated and uploaded `sqlite-schema-v36-inspection`, artifact ID `14818506827`, expiring 7 December 2026. The inspector and schema-v36 structural tests passed, including 47 tables, 25 explicit indexes, 139 triggers, zero views, contiguous migrations 1–36, integrity `ok`, and zero foreign-key violations.

The new adversarial tests reject both approver and release-authorizer identity rebinding when the forged identities belong to different active humans with the same roles. The migration test verifies clean v35→v36 upgrade. The fail-closed migration test seeds a v35 event with a forged approver identity and verifies the upgrade aborts without advancing `user_version` or installing the new trigger.

This confirms the local SQLite migration and test suite only. It does not close PostgreSQL parity, authenticated-session freshness, production staging service, or other Section 9 release gates. Later documentation-only commits do not change the tested code commit.

## Latest schema-v36 reconciliation-planner slice

The schema-v36 source-to-PostgreSQL mapping planner now validates the foreign-key inventory and derives a deterministic parent-first target import order. Cycles are only plannable when every constraint in the cycle is explicitly deferrable; non-deferrable cycles and unresolved/external dependencies fail closed. The result remains `INCOMPLETE` and cannot authorize an import.

Exact-head GitHub Actions [run #45363671654](https://github.com/Amiroudro/new-sngso/actions/runs/45363671654) passed on code head `273434f6362461b8166bf0499142af013fcebf4c`: **1,202 passed, 1 skipped** in the contract job; the separate PostgreSQL job passed **73 tests**. The schema-v36 inspection and artifact upload also succeeded.

Still open: an isolated bulk importer, exact row and audit-chain reconciliation, restricted import-role proof, restore rehearsal, and production cutover/HA acceptance. SQLite remains authoritative.


## Read-only schema-v36 dry-run command

Added the `sngso-schema-v36-dry-run` CLI. It combines the verified SQLite schema-v36 manifest, PostgreSQL target catalog inventory, and fingerprint-pinned reviewed mapping into a local no-overwrite report. Credentials are environment-only and connection failures do not print connection details. The report explicitly states that it has not inspected target row data, compared the target audit chain, or established cutover readiness. Outcomes remain `INCOMPLETE` for structurally valid plans and `BLOCKED` for missing/invalid mapping. Regression tests assert these safety fields. Exact-head GitHub Actions [run #45365107618](https://github.com/Amiroudro/new-sngso/actions/runs/45365107618) passed on `5a937749e2a89b5ab77c42dee49ed25d29baaa52`: **1,204 passed, 1 skipped** in the Python 3.12 contract suite and **73 PostgreSQL integration tests passed**. Schema-v36 inspection succeeded and uploaded artifact `14905419378`.

This closes only the structural preflight CLI slice. The isolated importer, row-by-row post-import reconciliation, audit-chain import/verification, restricted import role, restore rehearsal, and production/HA gates remain open.
