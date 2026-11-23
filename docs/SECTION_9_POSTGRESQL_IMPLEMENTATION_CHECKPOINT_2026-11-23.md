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
