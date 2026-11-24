# Schema-v36 deterministic SQLite export: implementation checkpoint

Status: **implemented in the Section 9 branch; CI execution and imported-target parity are not yet verified.**

Branch: `feat/section-9-durable-stage-checkpoints`  
PR: [#8](https://github.com/Amiroudro/new-sngso/pull/8)

## Added

- `src/sngso_engine/sqlite_export.py`: read-only schema-v36 source preflight and deterministic per-table streaming checksums.
- `tests/test_sqlite_export.py`: storage-class framing, batch-size determinism, source immutability, schema-version rejection, manifest installation, primary-key refusal, and audit-tamper detection.
- Public package exports for the manifest builder and SQLite value encoder.
- CLI entry point: `sngso-sqlite-export-manifest`.

## Checksum contract

Each SQLite value is encoded with a one-byte storage-class tag and an eight-byte big-endian payload length. INTEGER values use canonical base-10 ASCII; REAL values use IEEE-754 binary64; TEXT uses exact UTF-8; BLOB uses exact bytes; NULL has an empty payload. Non-finite REAL and Python booleans are rejected.

Rows are streamed in declared primary-key order. The table hash begins with a versioned header containing the table name, ordered column metadata, and primary-key definition. Each row has an explicit boundary marker and every value is length-framed. The digest is independent of fetch batch size and does not materialize a full table in memory.

The manifest includes schema/object inventory, migration versions, table counts, table schema hashes, row counts, row hashes, and the verified audit-chain tip. It is canonicalized and fingerprinted. Manifest output uses a same-directory temporary file and atomic hard-link installation, with overwrite refusal and restrictive file permissions.

## Safety and limitations

- Source database is opened with SQLite URI `mode=ro` and `PRAGMA query_only = ON`.
- One explicit read transaction pins schema, data, and audit queries to one snapshot.
- Schema version 36, contiguous migrations 1–36, SQLite integrity, foreign keys, and the exact reviewed object counts (47 tables, 25 explicit indexes, 139 triggers, zero views) are required.
- Tables without declared primary keys are rejected.
- The source audit chain is recomputed from sequence 1, including sequence continuity, predecessor links, canonical event hashes, and final tip.
- No PostgreSQL rows are written or compared by this component. This is the source-manifest engine, not the complete source-to-target dry-run, importer, or cutover.
- The source file's filename is recorded, not a whole-file byte digest. The logical table and schema checksums are the manifest's data-integrity evidence.
- No CI pass is claimed until a workflow run for the exact implementation commit is observed.

## Invocation

```bash
sngso-sqlite-export-manifest /path/to/registry.sqlite --output /path/to/schema-v36-manifest.json
```

For a diagnostic run with a different fetch batch size:

```bash
sngso-sqlite-export-manifest /path/to/registry.sqlite --output /path/to/manifest.json --batch-size 1
```

The command refuses an existing output path. Keep the manifest outside the source database directory when practical, and protect it as operational metadata.
