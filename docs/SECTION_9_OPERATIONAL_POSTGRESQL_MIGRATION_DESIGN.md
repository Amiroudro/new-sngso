# PostgreSQL Operational Storage Migration Architecture

Status: **design only; no PostgreSQL implementation or deployment is authorized by this document.**

Repository scope: `Amiroudro/new-sngso`. This design follows the current SQLite schema and transaction behavior in `src/sngso_engine/registry.py`, the checkpoint and lease stores, and the backend contract in Section 9.12.

## 1. Decision

Do not implement a PostgreSQL-only worker-lease adapter beside the current SQLite registry.

The supported multi-host design must move the complete consistency boundary to one transactional database and one transaction manager. The preferred target is a **single PostgreSQL-backed registry**, including all registry writes that participate in the current global audit chain. A narrower migration is possible only if the audit model is deliberately redesigned and separately accepted; it cannot claim to preserve the current globally ordered audit chain unchanged.

Until that migration is implemented and verified, SQLite remains the authoritative registry and its worker coordination is limited to processes sharing a local database file on one host. Do not run a split-brain or dual-write deployment.

## 2. What the current code actually couples

The current registry is schema version 33. Its migrations establish these relevant records:

| Current SQLite object | Introduced | Why it belongs in the migration boundary |
|---|---:|---|
| `audit_events` | 1 | One append-only, globally ordered hash chain used by registry writes |
| `pipeline_runs` | 26 | Parent lifecycle and immutable run-input identity |
| `pipeline_run_revisions` | 26 | Append-only lifecycle history |
| `pipeline_stage_checkpoints` | 32 | Durable stage head, provider operation handle, idempotency key |
| `pipeline_stage_checkpoint_revisions` | 32 | Append-only checkpoint history |
| `pipeline_worker_leases` | 33 | Current owner, token verifier, fencing generation, cancellation intent |
| `pipeline_worker_lease_revisions` | 33 | Append-only lease history |

The foreign-key relationships from checkpoint and lease records to `pipeline_runs` are part of the database contract. The application also composes run-state validation, lease mutation, checkpoint mutation, and audit append operations inside `SQLiteRegistry._transaction()`.

The transaction helper uses `BEGIN IMMEDIATE` for outer writes and savepoints for nested calls. The connection lock is held for the transaction scope. SQLite therefore serializes competing writers before they make state-changing decisions.

The audit writer reads the last event hash, calculates the next event hash from that predecessor and the canonical event payload, and inserts the next sequence row in the same transaction as the state change. The chain is not merely a collection of independent per-table logs.

There is one additional coupling that makes a narrow database split unsafe: ordinary source, permission, editorial, benchmark, and staging-registry writes also append to the same `audit_events` chain. Moving only pipeline tables and audit rows while leaving those write paths in SQLite would either require a distributed transaction or change the audit-chain semantics. The preferred migration therefore moves the registry and its audit chain together.

## 3. Target database boundary

### Preferred target

Move the complete SQLite registry schema at version 33 to PostgreSQL in one database. Keep the existing domain services and validation rules where practical, but replace direct SQLite-specific persistence behavior with a PostgreSQL repository/transaction implementation.

The first PostgreSQL release should not attempt to split the registry across databases or services. One transaction must be able to commit or roll back:

- the pipeline-run head and revision;
- the worker-lease head and revision;
- the stage-checkpoint head and revision;
- the corresponding audit event and audit-chain head;
- any related registry state required by the operation.

Other registry domains, their immutable histories, foreign keys, and audit writes move in the same cutover. This avoids a hidden cross-database transaction between a domain write and its audit record.

### Explicitly rejected designs

1. **Lease-only PostgreSQL:** rejected. Lease acknowledgement and run cancellation cannot be split from their shared audit and run-state transaction.
2. **PostgreSQL for operational tables, SQLite for the global audit chain:** rejected. A successful state change could commit without its audit event, or vice versa.
3. **Dual-write to SQLite and PostgreSQL:** rejected as a migration strategy. There is no distributed commit protocol or reconciliation contract that makes two independent commits atomic.
4. **Best-effort asynchronous audit mirroring:** not equivalent to the current in-transaction hash chain. It would require a new, explicitly accepted audit-delivery and gap-detection model.
5. **PostgreSQL advisory locks without transactional state checks:** insufficient. Locks coordinate callers but do not replace durable row constraints, revision checks, or atomic state transitions.

## 4. PostgreSQL schema mapping

Create a PostgreSQL baseline representing the current schema version 33, then evolve it through PostgreSQL-native migrations. Do not mechanically translate SQLite trigger SQL line by line.

| SQLite pattern | PostgreSQL target |
|---|---|
| `TEXT PRIMARY KEY` identifiers | `text PRIMARY KEY` initially, preserving existing identifiers |
| `INTEGER` revisions and counters | `bigint` with explicit non-negative / positive checks |
| JSON stored as canonical text | Preserve canonical serialized text for fingerprint-sensitive payloads; use `jsonb` only for derived/query projections unless canonicalization is explicitly controlled |
| `json_valid`, `json_extract`, `json_each` triggers | `jsonb` operators/functions and named validation functions, or application validation plus database constraints where expressible |
| SQLite partial indexes | PostgreSQL partial unique/index definitions with equivalent predicates |
| SQLite append-only triggers | Database roles without UPDATE/DELETE privileges plus defensive triggers for immutable tables |
| `AUTOINCREMENT` audit sequence | A transactional audit-head row and explicit sequence counter, not a bare sequence when commit order is required |
| `BEGIN IMMEDIATE` | Explicit row/advisory locking and transaction isolation, with every mutation guarded by database state |
| SQLite savepoints | PostgreSQL savepoints under one connection and transaction |

### Canonical payload and hash compatibility

Do not calculate existing audit hashes from PostgreSQL's default `jsonb::text` representation. JSONB may normalize key ordering, whitespace, and numeric representations. Existing hashes depend on the canonical serialization used by the application.

Retain the exact canonical JSON text for hash-bearing event payloads and any records whose fingerprints are defined over canonical JSON bytes. If JSONB is added for querying, treat it as a derived representation and verify that it corresponds to the canonical text. Hash compatibility tests must use known SQLite fixtures and independently recompute every event hash.

### Constraints and trigger port

Port every current invariant, not only the happy-path checks. At minimum, the schema inventory and migration review must cover:

- primary keys, foreign keys, composite foreign keys, uniqueness, and partial uniqueness;
- immutable record identity and immutable provider operation references;
- sequential revision increments and head/revision snapshot equality;
- allowed lifecycle transitions for runs, leases, and checkpoints;
- append-only revision and audit tables;
- JSON field allow-lists and indexed-column/payload consistency;
- actor, reason, timestamp, and fingerprint formats;
- the rule that a successful checkpoint has an output fingerprint and a non-successful checkpoint does not;
- cancellation-request immutability and lease-generation monotonicity.

Use PostgreSQL constraint triggers only where ordinary constraints cannot express the invariant. Keep critical transition checks inside the same transaction as the write. Revoke direct mutation privileges from the runtime role on immutable history tables.

## 5. Transaction and locking protocol

### Isolation level

Start with PostgreSQL `READ COMMITTED` plus explicit row locks and compare-and-swap predicates for the operations described below. Do not assume that selecting a row and later updating it is safe without a lock or revision predicate.

Use `SERIALIZABLE` only for a workflow whose invariant truly requires predicate serialization and whose callers implement bounded retry for serialization failures. A higher isolation level does not eliminate the need to handle ambiguous commits or remote provider outcomes.

### Run-scoped operations

For lease acquisition, heartbeat, release, cancellation, acknowledgement, checkpoint creation, checkpoint transition, and recovery:

1. Begin one database transaction.
2. Lock the pipeline-run row with `SELECT ... FOR UPDATE`; reject missing or terminal/incompatible runs.
3. Lock the lease head for the run when the operation uses lease ownership. For first-time lease creation, serialize creation by a transaction-scoped advisory lock derived from the stable run ID, then re-read the lease row. The advisory lock is a contention primitive; the primary key and revision predicates remain authoritative.
4. Lock checkpoint heads in ascending stage ordinal when more than one checkpoint is involved.
5. Validate the caller's token verifier, worker identity, fencing generation, expected revision, state, and expiry against the locked rows.
6. Apply head changes with explicit expected-revision predicates. Require exactly one updated row; zero rows means stale state and must abort.
7. Append the matching immutable revision rows.
8. Append the audit event and advance the audit-chain head in the same transaction.
9. Commit once. Do not perform provider/network/process work while holding database locks.

The same lock order must be followed by all code paths. A transaction must never lock the audit head and then request a pipeline, lease, or checkpoint row. For multi-run or multi-checkpoint work, acquire locks in deterministic run-ID and stage-ordinal order. Add deadlock tests and bounded handling for PostgreSQL deadlock/serialization errors.

### Audit-chain serialization

Add a singleton `audit_chain_head` row containing the latest committed sequence and event hash. Every transaction that appends an audit event must lock this row with `SELECT ... FOR UPDATE`, read the predecessor hash, calculate the canonical event hash, insert the event with the next sequence, update the head, and commit all of that with the associated domain mutation.

The row lock is held until commit, so two transactions cannot calculate successors from the same predecessor. Do not rely on a PostgreSQL sequence alone for chain order: sequence allocation order is not necessarily transaction commit order. The singleton head deliberately serializes audit appends. Measure the throughput cost; if it becomes unacceptable, a partitioned audit-chain design requires a separate governance decision and a new verification contract.

### Cancellation acknowledgement

The lease cancellation transition, pipeline-run transition to `cancelled`, lease revision, run revision, and audit event must commit or roll back together. A cancellation request remains durable across worker takeover. A new worker may observe the request but must not erase or silently reinterpret it.

### Provider calls

Never hold a PostgreSQL transaction open across a remote provider call or child-process lifetime. Commit the checkpoint's `executing` state first, call the provider with the deterministic idempotency key and fencing generation, then use a new transaction to reconcile and commit the result. If the worker loses its lease or the database outcome is uncertain, leave the checkpoint unresolved for provider reconciliation. A database rollback cannot undo a provider side effect.

## 6. Ambiguous commits and retries

A network failure during `COMMIT` does not prove that the transaction rolled back. Every mutating operation needs a defined reconciliation read using a new connection to the authoritative primary.

- **Heartbeat:** reread the lease head and compare worker, generation, token verifier, revision, and expiry. Do not blindly renew again from stale state.
- **Release/cancellation:** reread the lease and run heads. Treat the observed durable state as authoritative.
- **Checkpoint transition:** reread checkpoint head and revision history. Preserve `executing` or `indeterminate` if success cannot be proven.
- **Audit append:** verify the event ID and chain head. Never create a second logical event merely because the client did not receive the first commit response.
- **Lease acquisition:** the one-time bearer token is returned to the caller while only its verifier is stored. If the commit succeeds but the response is lost and the caller no longer possesses the token, do not reveal or reconstruct it from the database. Fail closed and allow the lease to expire or be safely released through a separately authenticated recovery path. A future implementation may add a carefully designed acquisition request identity, but it must not weaken one-time-token secrecy.

All retries must be bounded and operation-specific. Do not retry arbitrary transaction bodies that may have generated a new ID, token, event, or provider request. Generate stable operation identifiers before entering a retryable transaction, and use them to distinguish a duplicate request from a new logical operation.

## 7. Migration from SQLite schema versions 1–33

Do not run SQLite migrations against PostgreSQL. Build and review a PostgreSQL baseline schema corresponding to SQLite version 33, with a separate PostgreSQL migration ledger and ordered PostgreSQL migrations.

### Pre-migration inventory

Before any data movement:

- capture the SQLite `user_version` and all `schema_migrations` rows;
- run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`;
- verify the complete audit hash chain and record its final sequence/hash;
- count every table, including every revision/history table;
- export deterministic table-level checksums over primary-key-ordered canonical rows;
- inventory all SQLite triggers, indexes, foreign keys, and partial indexes;
- verify no unresolved schema migration or unsupported future schema version exists.

### Data movement

Use a maintenance window with application writes disabled. Take a verified SQLite backup. Copy the database in dependency order, preserving identifiers, revisions, timestamps, canonical payload text, audit sequence values, previous hashes, and event hashes.

Load into staging PostgreSQL tables or a disposable target database first. Do not expose a partially imported database to application workers. Apply all foreign keys and validation constraints, then verify row counts, key uniqueness, foreign-key consistency, revision continuity, append-only history consistency, payload/index-column agreement, and the entire audit chain.

A row-count match alone is not acceptance. Recompute all canonical fingerprints and audit hashes independently. Record a signed migration manifest containing source database identity, source schema version, backup fingerprint, table counts/checksums, audit head, target migration version, and verification result. The manifest is evidence, not a substitute for database verification.

### Cutover

1. Announce and enforce a write freeze. Stop worker admission and wait for in-flight transactions to finish or be safely reconciled.
2. Create and verify a final SQLite backup.
3. Run the pre-migration integrity and audit checks again.
4. Import the final snapshot into the isolated PostgreSQL target.
5. Run the full migration verification suite against that exact target.
6. Configure the application to use PostgreSQL, with no SQLite write path enabled.
7. Start one controlled worker in a non-publishing test environment; verify lease, checkpoint, audit, restart, and reconciliation behavior.
8. Expand worker admission only after all gates pass.
9. Retain the frozen SQLite backup as rollback evidence. Do not resume writes to it after PostgreSQL has accepted authoritative writes.

### Rollback boundary

Before PostgreSQL accepts authoritative writes, cutover can be abandoned and the frozen SQLite database reopened after verification.

After PostgreSQL has accepted writes, simply pointing the application back to the old SQLite snapshot loses those writes. Recovery then requires either a verified reverse migration/replay procedure or a forward fix on PostgreSQL. This design does not assume that reverse migration exists. The production runbook must define the point of no return and require an operator decision before crossing it.

## 8. Backup, restore, and failover requirements

A PostgreSQL deployment is not accepted merely because the primary database is shared by multiple hosts. The selected topology must document and test:

- synchronous/asynchronous replication and the resulting acknowledged-commit RPO;
- primary failover behavior and how clients discover the new primary;
- fencing of the old primary so two primaries cannot accept writes;
- connection-pool invalidation and reconnect behavior;
- backup encryption, key ownership, retention, off-site copies, and restore access;
- point-in-time recovery and the effect on audit-chain continuity;
- restore drills that verify schema, foreign keys, revisions, and the complete audit chain;
- clock synchronization and lease-expiry assumptions across worker hosts;
- behavior when the database is unavailable or a commit response is ambiguous.

A failover that can discard an acknowledged lease generation or audit append is incompatible with the monotonic fencing and audit guarantees unless the application explicitly reconciles and blocks work until integrity is re-established.

## 9. PostgreSQL CI and acceptance test matrix

The PostgreSQL implementation must run the backend conformance suite against a real disposable PostgreSQL service in CI. SQLite tests and mocked SQL adapters do not substitute for this.

| Test group | Required cases |
|---|---|
| Schema/migration | Empty database to current version; every supported upgrade path; interrupted migration rollback; unknown future version refusal |
| Lease contention | Independent connections and processes race for one run; exactly one owner; generation increments on takeover |
| Heartbeat/CAS | Concurrent renewals; stale revision; stale token; stale worker after takeover |
| Cancellation | Request survives expiry/takeover; acknowledgement atomically changes run, lease, revisions, and audit |
| Checkpoints | Concurrent creation/transition; unique idempotency key; immutable operation handle; stage ordering |
| Audit chain | Concurrent unrelated writes; no fork, duplicate sequence, or missing predecessor; independent full-chain recomputation |
| Crash windows | Process death before commit, during commit response, after commit before response, and after provider response before local commit |
| Database faults | Connection reset, primary restart, deadlock, serialization failure, pool exhaustion, statement timeout |
| Failover | Supported failover mode preserves the documented RPO and fencing behavior; stale primary cannot accept writes |
| Restore | Backup restored to a clean environment; all hashes, references, revisions, and counts verify |
| Compatibility | Existing SQLite fixture databases at supported schema versions import without changing canonical payloads or audit hashes |
| Security | Runtime role cannot mutate immutable history; credentials are not logged; TLS and secret rotation work in deployment configuration |

Tests involving process crashes, database restart, and failover must use independent processes/connections, not just threads sharing one in-memory object.

## 10. Implementation sequence and exit gates

| Phase | Deliverable | Exit gate |
|---|---|---|
| A | Schema inventory and invariant catalogue for all SQLite migrations 1–33 | Every table, trigger, index, FK, and audit writer is accounted for |
| B | PostgreSQL baseline schema and migration ledger | Fresh database and migration tests pass |
| C | PostgreSQL registry transaction/repository layer | Existing domain and operational tests pass against PostgreSQL |
| D | Audit-chain implementation | Concurrent append and independent verification tests pass |
| E | Lease/checkpoint/run conformance | Independent-process contention, crash, and reconciliation tests pass |
| F | SQLite-to-PostgreSQL importer | Real schema-v33 fixture imports with exact hashes and integrity |
| G | CI service and fault-injection suite | Full test matrix runs on every relevant change |
| H | Deployment/backup/failover runbooks | Restore and failover drills provide recorded evidence |
| I | Controlled cutover rehearsal | Rehearsal meets data-integrity, downtime, and rollback criteria |

Do not start by implementing a lease adapter in isolation. Phase A must produce the complete invariant catalogue first, because the current registry has many domain-specific triggers and every audit-producing write shares the same chain.

## 11. Current status and non-goals

- SQLite schema v33 remains the only implemented storage backend.
- `WorkerLeaseBackend` is an API protocol, not a database abstraction for the entire registry.
- PostgreSQL schema, repository, driver, pool, importer, CI service, failover configuration, and deployment are **not implemented**.
- No distributed lease HA or multi-host acceptance is claimed.
- No live collection, scheduling, publication, release, merge, or deployment is authorized.
- Existing website, Android application, and published editions are outside this repository change and remain untouched.

**Decision:** keep the present SQLite boundary until the complete registry migration can be implemented and verified as one coherent project. The PostgreSQL path is an architectural plan, not a production-ready capability.


## 12. Schema inventory captured from the current registry source

A source scan of `src/sngso_engine/registry.py` at the Section 9 branch recorded **47 distinct table definitions, 25 distinct index definitions, and 131 distinct trigger definitions**. This is a static source inventory, not proof that a live database contains every object; the schema-v33 migration and integrity checks remain authoritative.

The complete table inventory is grouped below to make omissions visible during PostgreSQL porting:

- **Schema and audit:** `schema_migrations`, `audit_events`.
- **Source governance and permissions:** `sources`, `source_endpoints`, `permission_evidence`, `permission_grants`, `grant_endpoints`, `grant_evidence`, `grant_operation_evidence`, `permission_grant_revisions`.
- **Editorial domain and ingestion:** `domain_records`, `domain_record_revisions`, `collection_observations`, `ingestion_decisions`, `ingestion_current`, `story_decisions`, `story_decision_applications`, `timeline_events`, `perspectives`, `claim_relations`.
- **Learning objects and glossary:** `learning_objects`, `learning_object_revisions`, `glossary_entries`, `glossary_entry_revisions`.
- **Benchmark and human review:** `benchmark_artifacts`, `benchmark_principals`, `benchmark_adjudications`, `benchmark_review_assignments`, `benchmark_review_assignment_revisions`, `benchmark_review_identity_attestations`, `benchmark_scores`, `benchmark_study_protocols`, `benchmark_study_executions`, `benchmark_acceptance_reviews`, `benchmark_reviews`, `benchmark_review_revisions`.
- **Pipeline operations:** `pipeline_runs`, `pipeline_run_revisions`, `pipeline_stage_checkpoints`, `pipeline_stage_checkpoint_revisions`, `pipeline_worker_leases`, `pipeline_worker_lease_revisions`.
- **Edition artifacts and staging controls:** `edition_artifacts`, `edition_decisions`, `edition_release_authorizations`, `edition_staging_events`, `edition_staging_channel_state`.

The PostgreSQL port must also account for all 25 named indexes and all 131 named triggers in the source migration definitions, including partial uniqueness, JSON payload guards, immutable histories, lifecycle checks, and cross-record validation. The inventory should be regenerated from source as migrations evolve; do not maintain it by hand as the sole source of truth.

The table inventory contains more than the seven operational tables listed in Section 2. That difference is deliberate: the seven-table list identifies the core run/lease/checkpoint/audit records, while the full registry inventory identifies the additional tables that must move with the global audit chain if its present single-chain semantics are to remain intact.
