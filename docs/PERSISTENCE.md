# Persistence, Revisions, and Audit Trail

Status: Sections 1–5 foundation implemented to documented scope; Section 5 fixture suite passed in GitHub Actions run #44607783960 (297 tests).  
Storage engine: Python `sqlite3` and SQLite.  
Migration line: schema version 5.

## Design choices

The engine uses a modular monolith and SQLite for local development and early, low-volume operation. SQLite ships with Python, so this layer adds no paid database service and no new runtime package dependency. PostgreSQL is deliberately deferred until measured concurrency or operational needs justify it.

The repository ports in `src/sngso_engine/ports.py` define source-governance, editorial-record, and audit interfaces. `SQLiteRegistry` is the initial adapter; callers can depend on the protocols instead of importing SQLite details. The older source-observation and edition-export ports remain future interfaces. Section 4 adds a separate append-only collection-observation repository port.

The default `SQLiteRegistry()` uses an in-memory database. A persistent local database is opt-in:

```python
from sngso_engine import SQLiteRegistry

with SQLiteRegistry("data/sngso-registry.sqlite") as registry:
    source = registry.get_source("source:example-publisher")
```

The caller is responsible for creating the parent directory and protecting the database file and backups. Do not commit a production database, permission letters, private correspondence, or credentials to Git.

## Schema and migrations

Migration 1 creates:

- `sources`: publisher identity and status.
- `source_endpoints`: individually identified feeds, APIs, sitemaps, and pages.
- `permission_evidence`: evidence references, fingerprints, and review state.
- `permission_grants`: current time-bounded, endpoint-scoped permission decisions.
- `grant_endpoints`, `grant_evidence`, and `grant_operation_evidence`: relational scope and evidence links with composite foreign keys that prevent cross-source references.
- `permission_grant_revisions`: append-only grant history.
- `domain_records`: article, story, claim, factual evidence, and edition records.
- `domain_record_revisions`: append-only editorial record history.
- `audit_events`: append-only hash-linked operational and permission-decision events.

Migration 2 adds:

- `collection_observations`: immutable discovery/metadata observations linked by composite foreign keys to the exact source, endpoint, and permission grant. The row captures the grant revision, connector identity, observation time, locator, metadata, and decision reason.

Migration 3 adds:

- `http_method` on `collection_observations`, constrained to `GET` for the currently implemented collection transport. Existing observations migrate with `GET` as their method. Future method support requires a separately reviewed schema and permission policy change.

Migration 4 adds:

- `ingestion_decisions`: append-only, revisioned decisions linking a stored metadata observation to a distinct article identity, with deterministic duplicate fingerprint, candidate/confirmed duplicate relationship, canonical URI, explanation, and timestamp.
- `ingestion_current`: a current-pointer table. Updating this pointer does not rewrite history; every resolution appends a new decision and audit event.
- Expression and fingerprint indexes for source/canonical-URL identity and cross-source candidate lookup. Candidate queries consider only the latest current fingerprint for each article. Database triggers reject decisions that point to missing or mismatched articles/observations.

The applied schema version is stored in SQLite's `user_version` and in `schema_migrations`. Migrations run in an exclusive transaction. A failed migration rolls back; a database with a newer unknown schema version is rejected rather than downgraded.

Foreign-key enforcement is enabled on the connection. File databases use WAL mode and a bounded busy timeout. Writes use `BEGIN IMMEDIATE`, so separate registry instances serialize writes rather than silently overwriting each other.

## Cross-record validation

JSON Schema validates the shape of each record. The registry adds checks that JSON Schema cannot express:

- endpoints must belong to an existing source;
- permission evidence must belong to an existing source;
- every grant endpoint and evidence reference must exist and belong to the grant's source;
- an approved grant requires an active source, active endpoints, and verified evidence;
- evidence supporting an allowed operation must be verified and included in the grant's evidence list;
- an article's source and optional permission reference must agree;
- stories can reference only stored articles;
- claims must reference a stored story and valid source/article provenance;
- factual evidence must reference a stored claim and a source consistent with its article;
- editions can reference only stored stories.

These checks run inside the same transaction as the insert or revision. A failed insert leaves no partial record or audit event.

## Permission lifecycle and decisions

Grant transitions are explicit:

- pending can become approved, denied, suspended, or revoked;
- approved can become suspended or revoked;
- suspended can return to approved after a fresh validation, or become revoked;
- denied and revoked are terminal.

Approval records reviewer identity and timestamp. Revocation records its timestamp and reason. Every transition appends a grant revision and an audit event. An expired, suspended, revoked, out-of-scope, or evidence-invalid grant is denied.

`evaluate_grant` resolves the persisted source, endpoint, grant, and evidence records before calling the operation evaluator. It records the operation, source, endpoint, result, reason, evaluation time, and policy version. This is the decision service used by the Section 4 collection orchestrator. The orchestrator checks permission before invoking a connector. The persistent observation sink rechecks the grant revision, active source/endpoint, evidence state, and effective permission at storage time, so a grant revoked during connector execution cannot be used to store its result.

## Editorial record revisions

Article, story, claim, factual evidence, and edition payloads are stored as typed records validated against their existing contracts. Section 5 ingestion creates or reuses an article inside the same transaction as its append-only identity decision; source observations remain intact and separate. Newer observations refresh mutable article metadata through immutable revisions, while older/equal-time observations cannot overwrite it. The article's source and canonical URL identity links cannot be rewritten. Non-edition records can be revised with an expected revision number, actor, and reason. Stale writes fail rather than overwriting newer records. Source links and original observation/creation timestamps are protected against rewriting.

Edition snapshots are immutable through the repository API, including published editions. A correction must be represented as a new edition with the appropriate supersession relationship, not by editing the old snapshot.

## Audit integrity

Every source, endpoint, permission evidence, grant, grant transition, domain record, domain revision, permission decision, and persisted collection observation appends an audit event. Each event hashes its canonical payload and the previous event hash. SQLite triggers reject ordinary UPDATE and DELETE operations on audit events and revision history. `verify_audit_chain()` checks the retained sequence and hashes. `audit_checkpoint()` exposes the current sequence and chain-tip hash so an operator can store that pair in an independently controlled system. Comparing a later database against that external checkpoint can detect deletion or truncation of the chain tail. A checkpoint stored only in the same database does not help: the same privileged operator could rewrite both.

The verifier alone cannot detect tail truncation. If the last events are removed after local triggers are bypassed, the remaining prefix still forms a valid chain. This is **tamper-evident, not tamper-proof**. A person with direct control of the database file can disable triggers or rewrite the file. Production deployment will need protected backups, restricted filesystem access, and potentially external audit anchoring. The current implementation does not claim cryptographic protection against a fully privileged database operator.

## Deliberate limitations

- No live publisher connector or network collection is implemented.
- No real publisher is pre-approved; the registry starts empty.
- Evidence records store references and fingerprints, not copies of legal documents or full source articles.
- Human reviewer identity is recorded, but authentication and reviewer-role authorization are not yet implemented.
- Source and endpoint status changes do not yet have dedicated lifecycle APIs.
- Pipeline runs, timelines, perspectives, and learning objects are not yet persisted. The new collection-observation table persists only permission-checked discovery/metadata results; it is not a general-purpose article-content store.
- Cross-record validation is implemented for the listed relationships only; it is not a substitute for the full publication gate.
- SQLite is suitable for local and early low-volume use, not yet validated for a multi-user hosted production workload.
- Backups, restore drills, encryption-at-rest policy, retention automation, and external audit anchoring remain future operational work.

## Verification

The repository includes fixture tests for migration idempotency and rollback, cross-source reference rejection, evidence review requirements, grant lifecycle, permission decisions, audit chain integrity, append-only revisions, optimistic concurrency, concurrent writes, permission-first connector gating, operation separation, provenance persistence, and revocation during connector execution.

The full pytest suite passed in GitHub Actions run [#44607783960](https://github.com/Amiroudro/new-sngso/actions/runs/44607783960) with **297 tests**, including schema migration 4, metadata ingestion, idempotency, cross-publisher duplicate candidates, reversible resolutions, article metadata revisions, permission-revocation checks, and concurrent ingestion.
