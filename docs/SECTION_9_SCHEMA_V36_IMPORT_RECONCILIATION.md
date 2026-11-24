# Schema-v36 SQLite → PostgreSQL Import and Reconciliation Contract

Status: **implementation target; no import, cutover, or production write is authorized by this document.**

Repository: `Amiroudro/new-sngso`  
Implementation branch: `feat/section-9-durable-stage-checkpoints`  
Parent PR: [#8](https://github.com/Amiroudro/new-sngso/pull/8)

## 1. Purpose and non-goals

This contract defines the next Section 9 engineering milestone: a repeatable, read-only dry-run and an isolated import/reconciliation path for a verified SQLite schema-v36 registry.

The source SQLite registry remains authoritative until an explicit cutover decision. The importer must never dual-write, mutate the source, enable workers, publish editions, or silently repair source data. A successful dry-run is evidence about a proposed import, not permission to cut over.

The first implementation is a deterministic offline/maintenance-window tool. It is not a live replication system and does not provide zero-downtime migration.

## 2. Required command model

The implementation should expose separate commands with separate permissions:

- `inspect-source`: read-only SQLite inventory and source validation.
- `dry-run`: compare a source snapshot with a disposable PostgreSQL target without writing target rows.
- `import`: load a frozen, verified source snapshot into an isolated empty PostgreSQL database.
- `verify-import`: independently reconcile the imported target against the source manifest.
- `restore-check`: restore a verified backup to a new path/database and rerun the same verification.

The default command must be `dry-run`. Destructive flags, target truncation, schema reset, or automatic cutover are prohibited. Import must refuse a non-empty target unless it is an explicitly created, uniquely identified import namespace with no application connections.

All commands must require explicit source and target identities. Credentials are supplied through the process environment or an approved secret provider, never command-line arguments, manifests, logs, or generated reports.

## 3. Source snapshot preconditions

Before inventory or export:

1. Open the existing SQLite database using URI `mode=ro`; never create a missing source.
2. Begin one read transaction and pin all schema, table, row, and audit queries to that snapshot.
3. Require `PRAGMA user_version = 36` and contiguous `schema_migrations` versions 1–36.
4. Require `PRAGMA integrity_check` to return exactly `ok`.
5. Require an empty `PRAGMA foreign_key_check` result.
6. Recompute the complete audit chain from sequence 1, checking contiguous sequence, predecessor links, canonical event hashes, and the final tip.
7. Require the live inventory to match the reviewed schema-v36 expectation: 47 tables, 25 explicit indexes, 139 triggers, and zero views. The exact SQL inventory digest must be captured, not inferred from counts.
8. Refuse unsupported future schema versions, migration gaps, unknown tables, missing expected tables, malformed identifiers, or a source audit checkpoint that disagrees with an independently supplied checkpoint.

The source must be frozen for the final export/import pass. A read-only snapshot can be internally consistent while later source writes occur; therefore the final source audit tip and table checksums must be re-read after the maintenance write freeze and compared with the import manifest.

## 4. Deterministic inventory and checksum format

The manifest format is versioned: `sngso-registry-import-manifest-v1`.

For every source table, record:

- exact table name;
- ordered primary-key column names;
- row count;
- ordered column names and SQLite declared types;
- deterministic row checksum;
- schema/object digest relevant to the table;
- import dependency ordinal;
- whether the table is append-only or revision-backed.

Rows are read in primary-key order. Tables without a declared primary key must be explicitly classified and use a stable, reviewed ordering key; the importer must refuse ambiguous ordering rather than rely on SQLite's incidental row order.

The checksum algorithm must be specified in code and frozen by golden fixtures. It must encode SQLite storage classes distinctly (NULL, INTEGER, REAL, TEXT, BLOB), preserve exact UTF-8 text and canonical JSON text bytes, and length-prefix each encoded value before feeding the row digest. The table digest is a streaming SHA-256 over a versioned table header and the ordered row encodings. Do not hash delimiter-joined strings, Python repr output, JSONB text, or driver-dependent row representations.

A second, target-side canonicalization layer must explicitly normalize PostgreSQL types back to the source logical representation. In particular, boolean/integer, timestamp/text, numeric/real, JSONB/text, and bytea/BLOB conversions require declared column-level rules. If a source column has no unambiguous target conversion, the table is not importable and the dry-run must fail.

The manifest contains no secrets or row payloads. It may contain table names, counts, digests, schema version, audit tip, source file identity/fingerprint, target migration version, importer version, and timestamps. The manifest itself is canonicalized and SHA-256 fingerprinted. It must be written atomically to a new path and must refuse overwrite.

## 5. Dependency ordering and data preservation

Import order is generated from the PostgreSQL foreign-key graph, not a hand-maintained guess. Parent tables precede dependent tables. Cycles must be identified explicitly and accepted only when every edge in the cycle is deferrable and the transaction can safely defer it. Otherwise import fails before writing.

Preserve all source identifiers, revision numbers, timestamps, status values, actor/reason attribution, canonical JSON text, provider operation references, audit sequence values, predecessor hashes, and event hashes. Do not regenerate IDs, timestamps, audit events, revisions, or fingerprints during import.

The importer must not invoke application-level create/revise methods: those methods generate new revisions and audit events. It must use a reviewed bulk-load path designed for historical state preservation. Trigger suppression, if required to avoid generating duplicate history, is restricted to the isolated import transaction and must never disable foreign-key enforcement. The import process must record exactly which trigger classes were suppressed and why; after re-enabling them, the target is verified from independent read connections. A trigger that cannot safely be suppressed and restored under the chosen database role blocks implementation.

No imported target is exposed to workers or application traffic before verification succeeds.

## 6. Dry-run report

A dry-run must not change either database. It produces a machine-readable report and a human-readable summary containing:

- source schema version, migration ledger, integrity result, FK result, schema digest, table/object counts, audit tip, and audit-chain result;
- PostgreSQL target identity, server version, migration ledger, expected target schema version, and whether the target is empty;
- per-table source/target counts and checksums where a target snapshot exists;
- missing/extra tables and columns, incompatible types, FK graph issues, unsupported constraints, and trigger-semantic coverage gaps;
- audit-chain source and target sequence/hash comparison;
- explicit `READY`, `BLOCKED`, or `INCOMPLETE` outcome with stable machine-readable reason codes.

`READY` means only that the inspected source and target are structurally eligible for an isolated import. It does not mean the import has occurred or that production cutover is accepted. Any omitted table, unverified checksum, unavailable target query, unsupported conversion, or incomplete audit check must result in `BLOCKED` or `INCOMPLETE`, never a warning-only success.

## 7. Import transaction and restart behavior

The first implementation should import into a newly provisioned disposable PostgreSQL database or schema that has no application principals or workers. It must not import into a shared live database.

The import run has a stable run ID and records a durable phase ledger outside the target data transaction. Phases are: source-verified, target-verified-empty, schema-applied, data-loaded, constraints-validated, trigger-restored, reconciliation-passed, sealed. Every phase records a source-manifest fingerprint and target identity.

A failed import is not resumed by blindly replaying statements. The importer must either:
- roll back the entire data transaction and prove the target is still empty except for the reviewed schema/phase ledger; or
- mark the target contaminated and require a fresh disposable target.

Do not implement per-table resume until table-level atomicity, dependency ordering, duplicate handling, and audit-chain anchoring have a separately reviewed design. The initial safe default is one transaction for the complete data load where PostgreSQL limits permit it. If the complete import cannot fit safely in one transaction, stop and design a staged immutable-load protocol rather than weakening atomicity implicitly.

## 8. Reconciliation gates

After import, use a new independent PostgreSQL connection and verify all of the following:

1. Every expected table exists exactly once; no unexpected application table is present.
2. Every table's row count and canonical checksum equals the source manifest.
3. Primary-key and unique constraints have no violations.
4. Every foreign key validates; no orphan rows exist.
5. All revision histories are contiguous from their defined initial revision and each current head matches its latest immutable revision snapshot.
6. Append-only tables retain all source rows and no extra rows.
7. JSON payloads agree with indexed relational columns under the same validation rules used by the registry.
8. All imported audit events are recomputed independently from canonical payload bytes; sequence, predecessor chain, event hash, and singleton chain head equal the source tip exactly.
9. The target schema/migration ledger and installed trigger/function inventory match the reviewed PostgreSQL migration set.
10. A second dry-run against the imported target reports exact parity and no unresolved discrepancy.

Reconciliation must be read-only. It must not fix, normalize, backfill, or delete mismatched rows. Any mismatch contaminates the target and blocks cutover.

## 9. Rollback and point of no return

Before PostgreSQL accepts authoritative application writes, rollback means abandoning the isolated target and reopening the frozen, verified SQLite source. The source remains unchanged throughout import.

The cutover runbook must define a named point of no return. After PostgreSQL accepts the first authoritative write, switching back to the pre-cutover SQLite snapshot would lose committed data and is forbidden. Recovery after that point requires a separately tested reverse replay or a forward repair on PostgreSQL. No reverse importer is assumed.

Required failure-injection cases include process termination before import, during bulk load, after data load but before constraints, after trigger restoration, during reconciliation, and after the final verification report is written. In every pre-cutover failure, the source must remain byte-for-byte unchanged and the target must either be empty or explicitly marked contaminated and unusable.

## 10. Required tests and evidence

The importer is not accepted on unit tests alone. The minimum suite includes:

- golden checksum fixtures for all SQLite storage classes, Unicode, embedded NUL text, empty text/blob, NULL, integer boundaries, real values, and canonical JSON;
- stable results across repeated dry-runs and different fetch batch sizes;
- refusal of missing source, wrong schema version, migration gaps, integrity failure, FK violations, audit tampering, audit truncation, schema drift, and external checkpoint mismatch;
- refusal of ambiguous ordering keys, unsupported type conversions, unexpected target objects, non-empty target, and non-deferrable FK cycles;
- exact source/target parity for every table, including all revision/history tables;
- independent recomputation of every audit event hash and final chain tip;
- trigger/constraint validation after bulk import;
- rollback/failure injection at every import phase;
- no source writes, no audit appends, no worker admission, and no application access before sealing;
- repeat import into a fresh target produces the same manifest and target checksums.

A PostgreSQL 16 service in CI must run the real importer and reconciliation tests using independent connections. At least one restricted import principal test must prove that the importer has only the narrowly required staging privileges and cannot mutate the source or production registry. CI evidence must identify the exact tested commit, source fixture digest, PostgreSQL version, manifest fingerprint, and test counts.

## 11. Implementation sequence

1. Freeze the source schema-v36 table/column/PK/FK inventory and obtain the schema-v36 CI artifact as the fixture authority.
2. Implement the versioned SQLite row encoder and streaming per-table checksum, with golden fixtures and stable dry-run output.
3. Implement PostgreSQL schema introspection and explicit source-to-target type mapping; fail closed on any unknown mapping.
4. Implement a read-only reconciliation planner that compares source inventory to target schema and emits stable reason codes.
5. Implement import dependency ordering and cycle analysis from actual target FK metadata.
6. Implement isolated bulk import with a durable phase ledger and failure-injection seams.
7. Implement independent target reconciliation, including full audit-chain recomputation and head comparison.
8. Add backup/restore rehearsal and rollback evidence against the exact imported fixture.
9. Only then update Section 9 acceptance status. Do not mark the PostgreSQL consistency boundary complete until all 47 tables and all 139 SQLite trigger semantics have explicit PostgreSQL enforcement and direct-SQL evidence.

## 12. Current evidence boundary

The current branch has PostgreSQL-native migrations through 0023 and real PostgreSQL integration coverage for the implemented registry slices. That does not establish import parity. SQLite schema v36 remains authoritative. The existing PostgreSQL integration fixtures are newly constructed test data, not an imported production-like v36 registry.

No import, restore rehearsal, cutover, production role provisioning, multi-host worker admission, or deployment is claimed by this contract.

## 13. Implemented reconciliation-planner slice (24 November 2026)

The read-only schema-v36 planner now derives a deterministic target import order from the inspected PostgreSQL foreign-key inventory:

- Parent tables are ordered before dependent child tables, regardless of the mapping file order.
- A cyclic dependency is accepted for planning only when every constraint participating in that cycle is explicitly deferrable; the exact constraints are returned for deferred validation.
- A non-deferrable cycle, external-schema dependency, unmapped parent, malformed/duplicate constraint record, or missing foreign-key inventory blocks the plan and returns no import order.
- The planner does not connect to either database, execute SQL, import rows, defer constraints, mutate schema, or authorize cutover.

Regression cases cover reversed parent/child mapping order, a deferrable self-cycle, and rejection of a non-deferrable self-cycle. GitHub Actions [run #45363671654](https://github.com/Amiroudro/new-sngso/actions/runs/45363671654) passed on code head `273434f6362461b8166bf0499142af013fcebf4c`: **1,202 passed, 1 skipped** in the contract job and **73 PostgreSQL integration tests passed**. The run also completed live schema-v36 inspection.

The next import milestone remains separate: isolated bulk load with phase ledger, trigger/constraint restoration, independent per-table row checksum reconciliation, and exact audit-chain import/verification. No data was imported and SQLite remains authoritative.
