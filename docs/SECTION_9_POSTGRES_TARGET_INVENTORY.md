# Section 9 PostgreSQL Target Inventory

Status: **read-only inventory implemented; import readiness intentionally incomplete.**

## Implementation

`src/sngso_engine/postgres_target_inspection.py` exposes
`inspect_postgres_import_target(connection)`. It requires an idle psycopg 3
connection and executes the inspection in one `REPEATABLE READ READ ONLY`
transaction. It does not apply migrations, create objects, append audit events,
or modify target rows.

The report captures:

- database, role, active schema, and PostgreSQL server version;
- tables and ordered column metadata, including types, nullability, defaults,
  identity and generated-column flags;
- primary, unique, check, and foreign-key constraints, including validation
  and deferrability;
- a structured foreign-key edge list with ordered source and referenced columns;
- sequence names and owners, plus index definitions;
- non-internal trigger definitions, enabled states, and function definitions;
- the installed PostgreSQL migration ledger compared against all packaged
  migration names and SHA-256 checksums;
- the singleton audit-chain head, when present, with sequence/count/min/max and
  anchor consistency checks;
- a deterministic SHA-256 digest of the captured inventory.

The public API is exported from `sngso_engine`. PostgreSQL integration tests
exercise the real migration fixture, repeatability, ledger parity, audit-head
capture, and rejection of a connection that is already inside a transaction.

## Deliberate readiness boundary

The report always returns `INCOMPLETE` and includes
`SOURCE_TARGET_MAPPING_NOT_REVIEWED`. This is intentional. The repository does
not yet contain an approved, column-level mapping from all 47 SQLite schema-v36
tables to PostgreSQL target relations and types. A catalog inventory is not
proof of semantic parity. In particular, matching names or superficially
compatible PostgreSQL types cannot establish preservation of SQLite storage
classes, revision histories, trigger semantics, or audit hashes.

No data import, target mutation, source mutation, worker admission, or cutover
is performed or authorized by this implementation.

## Remaining work

1. Freeze the schema-v36 source table/column/PK/FK inventory from the verified
   SQLite manifest.
2. Produce and review an explicit source-to-target mapping, including per-column
   canonicalization rules and unmapped-table handling.
3. Extend the dry-run planner to compare every mapped relation and reject
   missing, extra, incompatible, or semantically uncovered objects.
4. Add dependency ordering and independent target-side row/audit checksums.

The PostgreSQL target inventory is a prerequisite for reconciliation, not the
reconciliation itself.


## Structural reconciliation planner

`src/sngso_engine/postgres_reconciliation.py` adds a pure, non-connecting planner.
It accepts a source manifest, target inventory, and explicit mapping document.
The mapping is pinned to the exact source manifest, source schema fingerprint,
and target inventory fingerprint, and must include reviewer approval metadata.
The mapping document also carries a canonical SHA-256 fingerprint, so changing
the mapping after review invalidates the supplied mapping digest.

The planner rejects missing/duplicate source table mappings, missing/duplicate
column mappings, unknown target relations or columns, unmapped source columns,
unmapped required target columns, unsupported conversion rules, conversion
rules incompatible with the target PostgreSQL type, and primary-key order
mismatches. The target inspector now exports ordered primary-key columns to
support that check.

A successful structural check is still reported as `INCOMPLETE`. The planner
does not read row values and therefore cannot prove conversion safety, row
counts/checksums, or audit-chain equivalence. Those remain explicit gates.


## Read-only mapped-row target fingerprinting

`src/sngso_engine/postgres_row_inspection.py` adds
`inspect_postgres_mapped_rows(connection, source_manifest, target_inventory,
mapping_spec, batch_size=512)`. It requires a structurally valid, fingerprint-
pinned, reviewer-approved mapping and an idle psycopg 3 connection.

The scan runs in one `REPEATABLE READ READ ONLY` transaction. Before reading
rows, it rechecks the active schema and PostgreSQL server version, every mapped
table's complete column catalog, ordered primary keys, and the packaged
migration ledger against the supplied inventory. It then streams mapped target
rows in primary-key order, encodes values according to the reviewed conversion
rule, and returns per-table row counts and SHA-256 fingerprints. It never
returns primary-key values or row payloads and performs no writes.

The target encodings are explicit: UTF-8 text, canonical bigint decimal text,
IEEE-754 binary64, decoded bytea bytes, and PostgreSQL's own `jsonb::text`
representation. Non-finite floating-point values and malformed target
representations fail closed. JSONB output is retained as PostgreSQL-rendered
text; this is a target-side fingerprint, not proof that the source JSON and
target JSONB have equivalent semantics.

The report always remains `INCOMPLETE`. A row-count match is only a necessary
condition. The target digest is not directly comparable to the original
SQLite storage-class digest. The next required implementation is a source-side
converted-row digest using the exact same type-specific encoding contract,
followed by equality checks and explicit JSONB semantic-normalization proof.
Audit-chain import and verification remain separate gates. No import or
cutover is performed.


## Converted source-to-target row proof (non-JSON fields)

`src/sngso_engine/sqlite_converted_row_inspection.py` adds
`inspect_sqlite_converted_rows(database, source_manifest, mapping_spec)`.
It opens SQLite in read-only mode, verifies schema version 36, contiguous
migration history, integrity and foreign keys, then rechecks each mapped
source table's schema fingerprint, row checksum and storage-class histogram
inside the same snapshot before streaming converted values.

For text, bigint, binary64 and bytea conversions, the source scanner uses the
same framed byte encoding as the PostgreSQL target scanner. It rejects source
storage-class mismatches, bigint overflow, non-finite reals, invalid UTF-8
scalar strings and U+0000 in PostgreSQL text. Text primary-key ordering is
accepted only for SQLite BINARY collation and is matched with PostgreSQL C
collation. This prevents row-stream hashes from depending on incompatible
locale sort orders.

`compare_converted_row_reports(source_report, target_report)` verifies the
report fingerprints and source-manifest/mapping pins, then compares row counts,
per-column hashes and the combined non-JSON row digest. It reports mismatches
without exposing row values. A real PostgreSQL integration test builds a
schema-v36 SQLite fixture and confirms exact converted non-JSON digest equality
against the migrated PostgreSQL test target.

JSONB columns remain explicitly pending. Their source-text digest is retained
for evidence, but it is not compared to PostgreSQL's normalized `jsonb::text`.
The comparison always returns `INCOMPLETE`; JSONB semantic equivalence and
audit-chain import/verification are still required. The test fixture is
synthetic and does not constitute a reviewed production mapping or an import.
