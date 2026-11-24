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
