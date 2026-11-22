# Section 9 Live SQLite Schema Inspection

Status: **implemented and CI verified against a freshly migrated schema-v34 database.**

The schema inspector turns the P0 inventory step into a repeatable read-only operation against an actual registry database. It does not infer the live schema from migration source text.

## Run

From the repository root with the project installed or on `PYTHONPATH`:

```bash
python -m sngso_engine.schema_inspection /absolute/path/to/registry.sqlite
```

Write a review artifact:

```bash
python -m sngso_engine.schema_inspection /absolute/path/to/registry.sqlite \
  --output /absolute/path/to/schema-v34-inspection.json
```

The target must already exist. The inspector opens it using SQLite URI `mode=ro` and pins all queries to one read transaction, so schema, migration history, and audit-tip results describe a consistent snapshot. It does not create a missing database or mutate the registry.

## Report contents

- SQLite `user_version` and ordered `schema_migrations` history, including a contiguous-version check.
- `PRAGMA integrity_check` output.
- `PRAGMA foreign_key_check` violations.
- Read-only verification of the complete retained audit hash chain using the registry's canonical fingerprint function.
- The actual audit tip (sequence and hash), with optional comparison against a checkpoint supplied by an independently controlled system.
- Exact SQL definitions for user tables, indexes, triggers, and views from `sqlite_master`.
- Deterministic SHA-256 of the ordered schema-object inventory.
- Object counts and a combined `healthy` result. The schema-v34 test fixture asserts exactly 47 user tables, 25 explicit indexes, 136 triggers, and zero views.

The report intentionally preserves SQL definitions, not just object names. Reviewers can inspect CHECK clauses, foreign-key actions, partial-index predicates, and trigger bodies.

## Limits

- The audit verifier checks links from the first retained event. Like the registry's existing verifier, it cannot detect deletion of the chain tail without a previously exported independent checkpoint.
- A healthy report does not prove semantic equivalence between SQLite and a future PostgreSQL implementation.
- The schema digest is a comparison aid, not a signature or proof of database authenticity.
- Do not publish inspection JSON if schema names or SQL definitions are considered sensitive in the deployment environment.
- Run against a consistent backup or a quiesced copy for migration review. SQLite read-only mode prevents writes by this tool, but it does not itself create a cross-system cutover snapshot.

## Test coverage added

- Current schema-v34 migration and object-presence assertions.
- Full SQL definition availability for representative tables and triggers.
- Read-only behavior and missing-file rejection.
- Detection of foreign-key violations.
- Audit-chain verification on a newly initialized database.
- Detection of migration-history gaps, altered retained audit events, and tail truncation using an externally supplied checkpoint.
- Consistent read-snapshot acquisition for all inspection queries.

The schema-v34 fixture is generated and inspected in CI. The next P0 increment is direct-SQL negative coverage for every invariant and independent PostgreSQL conformance tests.

## Verified CI baseline

GitHub Actions [run #45056334657](https://github.com/Amiroudro/new-sngso/actions/runs/45056334657) passed the full Python suite (**1,032 passed in 20.46 seconds**), generated a fresh schema-v34 database using the real migration runner, ran this inspector, and uploaded the resulting `sqlite-schema-v34-inspection` artifact.

The inspected database reported schema version 34, contiguous migration history 1–34, integrity `ok`, zero foreign-key violations, a valid retained audit chain, and exactly 47 tables, 25 explicit indexes, 136 triggers, and zero views. Its schema digest was:

```
76de0802628d5da3933305254c8f50eb490e35da233691f6b01292e7b2f13e6d
```

The artifact is retained until 6 December 2026. This is a clean migration fixture generated in CI, not a production database backup or a populated operational registry.


## Independent audit checkpoint comparison

The API accepts an externally retained checkpoint:

```python
from sngso_engine.schema_inspection import inspect_sqlite_schema

report = inspect_sqlite_schema(
    "/absolute/path/to/registry.sqlite",
    expected_audit_checkpoint={
        "sequence": 842,
        "event_hash": "sha256:...",
    },
)
assert report["external_audit_checkpoint_matches"] is True
assert report["healthy"] is True
```

The checkpoint must be obtained from an independently controlled system. Storing it only beside the registry does not protect against an attacker who can truncate both. If the external checkpoint does not match the current tip, `healthy` is false even when the remaining hash chain is internally valid. This specifically detects deletion of a valid chain tail, which ordinary link verification cannot detect.

The checker validates checkpoint shape before opening the database. Sequence zero requires a null hash; positive sequences require a non-empty hash. It does not connect to or write to the external checkpoint system.
