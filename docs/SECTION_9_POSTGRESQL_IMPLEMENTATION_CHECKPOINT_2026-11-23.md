# Section 9 PostgreSQL implementation checkpoint

Date: 23 November 2026  
Branch: `feat/section-9-durable-stage-checkpoints`  
Pull request: [#8](https://github.com/Amiroudro/new-sngso/pull/8)

This checkpoint supersedes the earlier statements in
`SECTION_9_OPERATIONAL_POSTGRESQL_MIGRATION_DESIGN.md` that the PostgreSQL
transaction/audit foundation and PostgreSQL CI service had not been implemented.
It does not supersede the design's requirement to move the complete registry
and its global audit chain as one consistency boundary.

## Implemented in this slice

- A top-level READ COMMITTED transaction wrapper that rejects nested or already
  active connections and propagates failures for rollback.
- A packaged, ordered PostgreSQL migration runner using a transaction-scoped
  advisory lock, checksum-verified migration ledger, and fail-closed handling
  of unknown future versions or modified applied SQL.
- Migration 0001 for the initial audit event table, singleton chain head,
  append-only UPDATE/DELETE triggers, and public mutation revocations.
- An audit append primitive that locks the chain head, verifies its durable
  anchor, inserts the next event, and advances the head with compare-and-swap.
- A PostgreSQL 16 GitHub Actions service job with tests using actual
  independent database connections.

## Real PostgreSQL evidence

GitHub Actions [run #1510](https://github.com/Amiroudro/new-sngso/actions/runs/45181711173)
passed on commit `dc41e4ecff807a3947c63b75a0cb72f7358ce6ec`.

The PostgreSQL integration job ran against PostgreSQL 16 and passed **22 tests
in 0.67 seconds**. The integration cases verify:

- packaged migration execution and repeated application against PostgreSQL;
- concurrent audit appends from independent connections, with contiguous
  sequence numbers and predecessor-hash continuity;
- rollback of both the audit event and chain head after an injected exception
  following the append;
- the transaction wrapper, audit primitive, and migration runner regression
  tests against the real service.

The separate SQLite/contract job passed **1,141 tests**, with the
PostgreSQL-only module skipped there because that job intentionally does not
install the optional PostgreSQL driver. The workflow also generated and
uploaded the schema-v36 inspection artifact
[14821522595](https://github.com/Amiroudro/new-sngso/actions/runs/45181711173/artifacts/14821522595).

## Explicit limits

This is a verified PostgreSQL audit-storage slice, not the Section 9
PostgreSQL consistency boundary.

Not implemented or accepted yet:

- the full SQLite schema-v36 PostgreSQL baseline and all 47 tables, 25 indexes,
  and 139 trigger-equivalent invariants;
- the PostgreSQL staging authorization/event/state/audit transaction;
- PostgreSQL run, checkpoint, lease, editorial, benchmark, and publication
  repositories;
- least-privilege application-role grants and adversarial direct-SQL tests
  under that role;
- SQLite-to-PostgreSQL import, canonical hash equivalence for existing data,
  backup/restore, failover, or cutover evidence.

The current migration's public revocations and append-only triggers are not a
substitute for a reviewed runtime-role privilege model. The real integration
tests run as the disposable CI database owner; they do not prove restricted
application-role security.

SQLite schema v36 remains the authoritative implemented registry. No live
collection, scheduling, publication, release, deployment, or merge is
authorized. The website, Android application, and published editions remain
untouched. PR #8 remains draft and unmerged.

## Next engineering gate

Implement and test the restricted PostgreSQL runtime-role model, then port the
staging transaction as one atomic operation. That operation must lock and
validate the artifact, approval, release authorization, principal role state,
and staging-channel head; insert the immutable staging event; advance the
staging state by revision CAS; append the global audit event; and commit once.
Independent-connection contention and injected failures at every write
boundary must prove that no partial state can survive.


## Follow-up: default-privilege boundary

A fourth real-PostgreSQL integration case now creates a non-owner, non-login
reader role, grants schema usage and SELECT on audit events only, and verifies
that UPDATE, DELETE, and TRUNCATE are denied on both audit tables. The role
also cannot directly update the singleton chain head. This test passed in
GitHub Actions [run #1512](https://github.com/Amiroudro/new-sngso/actions/runs/45182064103).

Run #1512 passed:
- PostgreSQL 16 integration job: **23 passed in 1.26 seconds**.
- Contract/SQLite job: completed successfully, including the schema-v36
  inspection artifact upload.

This verifies the default denied-mutation posture for a read-only role. It
does not define or prove the production writer role, because the append path
currently requires a controlled chain-head update. A production writer-role
design must allow the intended append operation without granting arbitrary
direct mutation of audit history or an unguarded chain-head rewrite.


## Follow-up: migration ledger continuity

The migration runner now rejects a ledger that is not an ordered prefix of
the packaged migration sequence. This prevents a database with a missing
intermediate migration from being treated as safely upgradeable. A regression
test injects a ledger containing versions 1 and 3 while version 2 is absent.

GitHub Actions [run #1515](https://github.com/Amiroudro/new-sngso/actions/runs/45182437009)
passed on the corrected code head:
- PostgreSQL 16 integration job: **24 passed in 0.58 seconds**.
- Contract/SQLite job: **1,142 passed, 1 skipped in 28.12 seconds**. The
  single skip is expected because the general job does not install the
  optional psycopg driver; the dedicated PostgreSQL job runs those tests.
- Schema-v36 inspection completed and uploaded successfully.


## Follow-up: database-enforced audit-head transitions

PostgreSQL migration 0002, `0002_audit_head_guards.sql`, adds defensive database
guards around the audit append protocol:

- An inserted event must extend the currently locked chain head by exactly one
  sequence and carry that head's hash as its predecessor.
- A chain-head update must advance exactly one sequence and match the event
  inserted at that sequence, including its predecessor link.
- The chain-head row cannot be deleted.

A restricted non-owner role is now integration-tested with only schema usage,
SELECT/INSERT on audit events, and SELECT/UPDATE on the chain head. It can
append through the normal application writer, but direct head reset, head
deletion, and skipped-sequence insertion are rejected.

GitHub Actions [run #1526](https://github.com/Amiroudro/new-sngso/actions/runs/45210381447)
passed on commit `2cd8666e42140a34e2330272fcb699d7ca591213`:
- PostgreSQL 16 integration: **27 passed in 1.17 seconds**.
- Contract/SQLite validation: completed successfully.
- Live schema-v36 inspection and artifact upload: completed successfully.

Security boundary still outstanding: migration 0002 enforces append ordering
and predecessor consistency, but it does not independently recompute the
application's canonical SHA-256 event digest inside PostgreSQL. The restricted
writer is therefore an authorized append principal, not an untrusted SQL
principal. Production role provisioning, credential separation, ownership,
default privileges, and the complete registry's privilege matrix still need
a reviewed implementation. This is not the complete runtime-role model and
does not complete the full schema-v36 port or staging transaction.


## Follow-up: foundational source-permission-domain registry port

PostgreSQL migration 0003, `0003_source_permission_domain_registry.sql`, ports
the first dependency layer of the SQLite v36 registry:

- sources and source endpoints;
- permission evidence and grants;
- grant-to-endpoint, grant-to-evidence, and operation-evidence links;
- immutable permission-grant revision snapshots;
- domain records and immutable domain-record revision snapshots.

The migration retains source-scoped composite foreign keys, restrict-delete
semantics, the initial registry's source/status indexes, non-empty identity
fields, JSON object payload checks, and the domain record's last-revision
metadata columns. PostgreSQL triggers reject UPDATE and DELETE on both
revision-history tables. The migration runner registers the new migration
with its normal SHA-256 checksum ledger.

Real PostgreSQL tests exercise valid source-bound grant relationships,
cross-source composite-FK rejection, and immutable grant/domain revision
snapshots. An initial CI attempt found a test setup collision: the invalid
cross-source relationship reused a composite primary key, so the PK constraint
correctly fired before the FK under test. The test was corrected to use a
distinct grant identity, isolating the intended FK behavior.

GitHub Actions [run #1528](https://github.com/Amiroudro/new-sngso/actions/runs/45211009613)
passed:
- PostgreSQL 16 integration suite: **29 passed in 1.33 seconds**.
- Contract validation: passed.
- Live schema-v36 inspection and artifact upload: passed.

This is a foundational slice, not the complete schema-v36 port. The remaining
tables, triggers, cross-record invariants, role matrix, and staging/checkpoint
transaction boundaries are still open.


## Follow-up: collection observations and ingestion decision registry

PostgreSQL migration 0004, `0004_collection_ingestion.sql`, ports the next
dependency layer from SQLite migrations 2-4:

- append-only collection observations, including the GET-only HTTP method
  constraint and source/endpoint/grant provenance;
- ingestion decisions with outcome, duplicate target, fingerprint, canonical
  URI, reason, and decision revision;
- the current-decision pointer, constrained by a composite
  (observation_id, decision_id) foreign key;
- source/time, endpoint/time, fingerprint, and article source/URI indexes.

The PostgreSQL decision trigger checks that the referenced article is
registered as an article and that its source and canonical URI match the
decision. It also requires the observation to belong to the same source and,
when a duplicate target is supplied, requires that target to be an article
from a different source. Observation and decision history are append-only;
the current ingestion pointer cannot be deleted.

The integration suite now verifies valid observation-to-decision provenance,
GET-only method enforcement, rejection of an article/source mismatch, and
immutability of observations, decisions, and current state.

GitHub Actions [run #1531](https://github.com/Amiroudro/new-sngso/actions/runs/45211327959)
passed:
- PostgreSQL 16 integration suite: **30 passed in 1.85 seconds**.
- Contract validation: passed.
- Live schema-v36 inspection and artifact upload: passed.

This still does not port the remaining story, timeline, learning, benchmark,
pipeline, checkpoint, lease, or edition/staging registry domains. Nor does it
complete the production role matrix or atomic staging workflow.
