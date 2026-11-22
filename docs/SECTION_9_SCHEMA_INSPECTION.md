# Section 9 Live SQLite Schema Inspection

Status: **implemented and CI verified against a freshly migrated schema-v33 database.**

The schema inspector turns the P0 inventory step into a repeatable read-only operation against an actual registry database. It does not infer the live schema from migration source text.

## Run

From the repository root with the project installed or on `PYTHONPATH`:

```bash
python -m sngso_engine.schema_inspection /absolute/path/to/registry.sqlite
```

Write a review artifact:

```bash
python -m sngso_engine.schema_inspection /absolute/path/to/registry.sqlite \
  --output /absolute/path/to/schema-v33-inspection.json
```

The target must already exist. The inspector opens it using SQLite URI `mode=ro`; it does not create a missing database or mutate the registry.

## Report contents

- SQLite `user_version` and ordered `schema_migrations` history, including a contiguous-version check.
- `PRAGMA integrity_check` output.
- `PRAGMA foreign_key_check` violations.
- Read-only verification of the complete retained audit hash chain using the registry's canonical fingerprint function.
- Exact SQL definitions for user tables, indexes, triggers, and views from `sqlite_master`.
- Deterministic SHA-256 of the ordered schema-object inventory.
- Object counts and a combined `healthy` result. The schema-v33 test fixture asserts exactly 47 user tables, 25 explicit indexes, 131 triggers, and zero views.

The report intentionally preserves SQL definitions, not just object names. Reviewers can inspect CHECK clauses, foreign-key actions, partial-index predicates, and trigger bodies.

## Limits

- The audit verifier checks links from the first retained event. Like the registry's existing verifier, it cannot detect deletion of the chain tail without a previously exported independent checkpoint.
- A healthy report does not prove semantic equivalence between SQLite and a future PostgreSQL implementation.
- The schema digest is a comparison aid, not a signature or proof of database authenticity.
- Do not publish inspection JSON if schema names or SQL definitions are considered sensitive in the deployment environment.
- Run against a consistent backup or a quiesced copy for migration review. SQLite read-only mode prevents writes by this tool, but it does not itself create a cross-system cutover snapshot.

## Test coverage added

- Current schema-v33 migration and object-presence assertions.
- Full SQL definition availability for representative tables and triggers.
- Read-only behavior and missing-file rejection.
- Detection of foreign-key violations.
- Audit-chain verification on a newly initialized database.

CI must pass these tests before P0 can be marked verified. The next P0 increment is a golden schema snapshot from a real schema-v33 fixture and explicit per-trigger semantic tests.

## Verified CI baseline

GitHub Actions [run #45054352627](https://github.com/Amiroudro/new-sngso/actions/runs/45054352627) passed the full Python suite, generated a fresh schema-v33 database using the real migration runner, ran this inspector, and uploaded the resulting `sqlite-schema-v33-inspection` artifact.

The inspected database reported schema version 33, contiguous migration history 1–33, integrity `ok`, zero foreign-key violations, a valid retained audit chain, and exactly 47 tables, 25 explicit indexes, 131 triggers, and zero views. Its schema digest was:

```
42fd958579890d61fa64b3e84d769eb9a3eadcd905a101f7465e3a094b28b499
```

The artifact is retained until 6 December 2026. This is a clean migration fixture generated in CI, not a production database backup or a populated operational registry.
