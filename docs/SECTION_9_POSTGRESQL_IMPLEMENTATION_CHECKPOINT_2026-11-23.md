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


## Follow-up: story decisions and timeline event registry

PostgreSQL migration 0005, `0005_story_timeline.sql`, ports SQLite
registry migrations 5-7 and the timeline review invariants from migration 10.

It adds append-only story merge/split decisions and their application records,
including decision and impact SHA-256 fingerprints. It also adds append-only
timeline events with same-story supersession constraints and a database
validation trigger. The trigger requires an active registered story, requires
a non-empty claim list whose claim records all belong to that story, enforces
reviewer/timestamp/note metadata for approved or rejected events, rejects
review metadata on unreviewed events, and prevents review timestamps from
predating event creation.

Integration tests cover a valid decision/application pair, a valid timeline
event, cross-story claim rejection, incomplete review metadata rejection,
and mutation attempts against all three append-only record families.

GitHub Actions [run #1535](https://github.com/Amiroudro/new-sngso/actions/runs/45212140902)
passed:
- PostgreSQL 16 integration suite: **31 passed in 2.49 seconds**.
- Contract validation: passed.
- Live schema-v36 inspection and artifact upload: passed.

The full story/perspective/claim-relations and learning/benchmark/pipeline/
edition registry families remain outstanding; this migration is not a complete
schema-v36 port and does not complete production privileges or atomic staging.


## Follow-up: perspectives and claim relationships

PostgreSQL migration 0006, `0006_perspectives_claim_relations.sql`, ports
SQLite registry migrations 8-9.

Perspectives retain same-story supersession, optional source attribution,
publisher-framing requirements, article/source/story membership validation,
claim/story membership validation, and review metadata invariants. Claim
relations retain same-story supersession, the allowed relationship vocabulary,
distinct claim endpoints, active-story and same-story endpoint checks, and
review metadata invariants. Both registries are append-only and indexed by
story and creation time.

Integration tests verify a valid publisher-framing perspective and claim
relation, reject publisher framing without its article, reject a self-relation,
and verify update/delete attempts cannot rewrite history.

GitHub Actions [run #1540](https://github.com/Amiroudro/new-sngso/actions/runs/45212454435)
passed:
- PostgreSQL 16 integration suite: **32 passed in 2.42 seconds**.
- Contract validation: passed.
- Live schema-v36 inspection and artifact upload: passed.

This continues the registry port but does not yet cover learning objects,
glossary, benchmark, pipeline/checkpoint/lease, or edition/staging records.
Production privilege provisioning and the atomic staging transaction remain
separate outstanding milestones.


## Follow-up: learning object and glossary revision lifecycle

PostgreSQL migration 0007, `0007_learning_glossary_revisions.sql`, ports the
base learning-object and glossary-entry tables and their revision lifecycle
from SQLite migrations 11-12.

Learning objects retain the supported content-kind vocabulary, language,
variant-group identity, review status, actor/reason metadata, and per-story
kind/language indexes. Their writes require an active registered story and
claim IDs belonging to that story. Initial records must begin in draft or
review-required state without review metadata. Updates preserve identity,
advance the revision exactly once, keep timestamps monotonic, and require
reviewer/timestamp/note metadata for approved or rejected states. Variant
groups cannot cross story or kind boundaries. Each insert/update atomically
creates a matching revision snapshot; objects cannot be deleted and revision
history is append-only.

Glossary entries retain language/part-of-speech/lemma-key uniqueness, review
state, immutable identity, monotonic timestamps, exact revision increments,
and automatic append-only revision snapshots. The same initial and reviewed
state rules apply.

Integration tests verify both initial snapshots and subsequent approved
revisions, reject skipped revision numbers, and reject mutation/deletion of
revision history and learning objects.

GitHub Actions [run #1545](https://github.com/Amiroudro/new-sngso/actions/runs/45212856556)
passed:
- PostgreSQL 16 integration suite: **33 passed in 2.79 seconds**.
- Contract validation: passed.
- Live schema-v36 inspection and artifact upload: passed.

Specialized MCQ option/key checks, bilingual editorial rubrics, vocabulary-to-
glossary matching, summary traceability, answer-evidence provenance, and the
additional SQLite migrations 24-25 evidence rules remain separate follow-up
work. This migration does not claim the complete learning subsystem port.


## Follow-up: benchmark artifact, principal, review, and adjudication core

PostgreSQL migration 0008, `0008_benchmark_review_core.sql`, ports the
benchmark artifact and review lifecycle foundations from SQLite migrations
13-18.

It adds immutable benchmark artifacts with language, canonicalization, JSON,
and SHA-256 shape checks; benchmark principals with explicit principal-kind
classification; revisioned benchmark reviews with identity/fingerprint
immutability, exact revision increments, monotonic timestamps, and automatic
append-only revision snapshots; and one immutable final adjudication per
benchmark. Adjudication uniqueness is enforced by both a validation trigger
and a database unique constraint, so concurrent attempts cannot create two
final adjudications for the same benchmark.

Integration tests cover artifact and principal creation, review revision
snapshots, final adjudication, duplicate adjudication rejection, artifact
mutation rejection, principal-kind immutability, and revision-history
immutability.

GitHub Actions [run #1548](https://github.com/Amiroudro/new-sngso/actions/runs/45213279901)
passed:
- PostgreSQL 16 integration suite: **34 passed in 3.42 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

This is the benchmark core only. Benchmark assignment/scoring support,
specialized learning evidence rules, pipeline runs, durable stage checkpoints,
leases, edition/release records, production runtime-role provisioning, and
the atomic staging transaction remain outstanding. The full schema-v36
PostgreSQL consistency boundary is not complete.


## Follow-up: benchmark assignments, identity attestations, and scores

PostgreSQL migration 0009, `0009_benchmark_assignments_identity_scores.sql`,
ports SQLite migrations 17, 19, and 20. The benchmark principal language and
classification fields from SQLite migrations 17-18 were already included in
PostgreSQL migration 0008, avoiding duplicate schema alterations.

The migration adds reviewer assignments bound to immutable artifact identity,
a partial unique index preventing more than one active assignment for the same
reviewer and artifact, sequential revision enforcement, and automatic
append-only assignment revision snapshots. It adds immutable identity
attestations uniquely bound to a review revision and principal, plus immutable
per-item scores with an assignment/item uniqueness constraint and payload to
indexed-column consistency checks.

Integration tests cover initial and subsequent assignment snapshots, active
assignment collision, duplicate identity attestation, duplicate score,
attestation mutation, score deletion, and assignment revision deletion.

GitHub Actions [run #1563](https://github.com/Amiroudro/new-sngso/actions/runs/45216122496)
passed:
- PostgreSQL 16 integration suite: **35 passed in 3.28 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

This ports the assignment/identity/score storage layer, not the complete
benchmark workflow. Benchmark study protocols, executions, acceptance reviews,
specialized learning evidence rules, pipeline runs/checkpoints/leases,
edition/release records, production runtime-role provisioning, and atomic
staging remain outstanding. No live operations were performed.


## Follow-up: benchmark study protocol, execution, and acceptance evidence

PostgreSQL migration 0010, `0010_benchmark_study_evidence.sql`, ports the
study protocol, execution, and independent acceptance-review records from
SQLite migrations 21-23.

It adds immutable, sequentially versioned study protocols with stable
benchmark/fingerprint and rubric binding; immutable execution records tied to
a protocol and methodologist identity; and immutable acceptance reviews
bound to the exact execution and protocol, with a unique acceptance decision
per execution. Payload-to-column consistency checks protect the indexed
identities and fingerprints.

Integration tests cover protocol version creation and skipped-version
rejection, execution creation, acceptance review creation and duplicate
execution rejection, plus update/delete immutability for all three record
families.

GitHub Actions
[run #1569](https://github.com/Amiroudro/new-sngso/actions/runs/45216711974)
passed on commit `6d5c0763ff7545a3ddd1e955565c8760f2981ae9`:
- PostgreSQL 16 integration suite: **36 passed in 4.13 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

This closes the PostgreSQL storage port for the benchmark study protocol,
execution, and acceptance-review records only. Specialized learning evidence
rules, pipeline runs/checkpoints/leases, edition/release records, production
runtime-role provisioning, and the atomic staging transaction remain
outstanding. No live operations were performed.


## Follow-up: specialized learning evidence provenance

PostgreSQL migration 0011, `0011_learning_specialized_evidence.sql`, ports
SQLite migrations 24-25.

Vocabulary learning objects now require a source-evidence ID and exact
evidence revision that resolve to an evidence domain record linked to one of
the object's claims. The claim must itself reference that evidence and its
source/article/observation provenance must match. Comprehension, analysis,
and MCQ question objects require non-empty, unique answer-evidence references;
every answer claim must be covered, and each reference must resolve to the
current supporting evidence revision, include a source locator, belong to
the object's claims, and retain the claim's source provenance.

The PostgreSQL integration test creates source-backed claim/evidence records,
accepts valid vocabulary and question objects, rejects a stale vocabulary
evidence revision, and rejects incomplete answer-claim evidence coverage.

GitHub Actions
[run #1576](https://github.com/Amiroudro/new-sngso/actions/runs/45217934166)
passed:
- PostgreSQL 16 integration suite: **37 passed in 3.36 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

This completes the specialized evidence-trigger port for SQLite migrations
24-25. It does not complete the remaining learning rules (including the
bilingual editorial rubrics and summary traceability if/where separately
defined), pipeline runs/checkpoints/leases, edition/release records,
production runtime-role provisioning, or atomic staging.


## Follow-up: pipeline runs and durable stage checkpoints

PostgreSQL migration 0012, `0012_pipeline_runs.sql`, ports SQLite migration
26. It adds the pipeline-run head and revision history, indexed status/start
lookup, payload-to-indexed-field checks, immutable run identity and inputs,
sequential revisions, legal lifecycle transitions, and append-only history.
The database guard also binds retry attempt/parent identity, matching the
stronger immutability checks in the existing SQLite runtime method.

PostgreSQL migration 0013, `0013_pipeline_stage_checkpoints.sql`, ports
SQLite migration 32. It adds run-bound stage checkpoints, unique idempotency
keys, provider operation references, status/output invariants, a restricted
metadata vocabulary, automatic immutable revision snapshots, legal status
transitions, and deletion protection. Once a provider operation reference is
recorded, it cannot be replaced.

GitHub Actions
[run #1584](https://github.com/Amiroudro/new-sngso/actions/runs/45218622755)
passed:
- PostgreSQL 16 integration suite: **39 passed in 6.00 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

This verifies the pipeline-run and stage-checkpoint storage layer. Worker
leases (SQLite migration 33) are the next pipeline persistence migration;
edition artifacts, approvals, release authorizations, staging state/events,
production role provisioning, and the atomic staging transaction remain open.


## Follow-up: fenced worker lease lifecycle

PostgreSQL migration 0014, `0014_pipeline_worker_leases.sql`, ports SQLite
migration 33.

It adds one lease head per pipeline run and append-only lease revisions.
Database guards enforce sequential revision increments, stable run identity,
legal active/heartbeat/release/expiry/cancellation transitions, and
fencing-token advancement with a fresh token on reacquisition. An active lease
cannot be taken over directly; a released or expired lease can be reacquired
only with the next fencing token. Cancellation request metadata cannot be
rewritten once recorded. Lease heads and revision history cannot be deleted
or mutated.

Integration tests exercise acquisition, heartbeat, release, reacquisition
with a higher fencing token, rejection of an attempted takeover while active,
and append-only revision protection.

GitHub Actions
[run #1587](https://github.com/Amiroudro/new-sngso/actions/runs/45218836982)
passed:
- PostgreSQL 16 integration suite: **40 passed in 6.00 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

This verifies the PostgreSQL pipeline run/checkpoint/lease storage slice.
It does not implement a production worker scheduler, lease acquisition API,
heartbeat/reconciliation service, or complete retry orchestration. Edition
artifacts, approval decisions, release authorizations, staging events/state,
runtime-role provisioning, and the atomic staging transaction remain open.


## Follow-up: edition artifacts, approvals, and release authorization

PostgreSQL migrations 0015-0018 port SQLite migrations 27-30:

- Migration 0015 adds immutable edition artifact/manifest records and checks
  that the indexed manifest identity, revision, and payload fingerprint match
  the stored manifest.
- Migration 0016 adds append-only approval decisions bound to the exact
  immutable artifact and an active human principal with the
  `edition_approver` role. The decision is explicitly scoped to staging
  candidate review and cannot authorize publication.
- Migration 0017 blocks direct insertion of published edition snapshots and
  makes edition domain records immutable.
- Migration 0018 adds append-only release authorizations bound to the exact
  approved artifact and decision. The release authorizer must be a distinct,
  active human principal with the `edition_release_authorizer` role.

Integration tests cover artifact/manifest binding, valid human approval,
rejection of an inactive approver, independent release authorization,
rejection of same-person approval/authorization, immutable records, and the
controlled publication boundary.

GitHub Actions
[run #1596](https://github.com/Amiroudro/new-sngso/actions/runs/45219425597)
passed:
- PostgreSQL 16 integration suite: **43 passed in 10.66 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

These migrations establish the edition artifact and authority records. They
do not by themselves perform export verification, authorize publication, or
complete the atomic staging/audit transaction.


## Follow-up: edition staging events and channel state

PostgreSQL migration 0019, `0019_edition_staging_state.sql`, ports SQLite
migration 31.

It adds append-only staging events and the singleton staging-channel state.
The event guard locks the current channel-state row and validates the exact
artifact, approval decision, independent release authorization, three
distinct active human authorities (approver, release authorizer, and staging
operator), export-package identity, publication-disabled flags, sequential
state revision, previous-current/LKG references, and promote/rollback
eligibility. The state guard only permits a one-revision advance from a
matching inserted event. Event history and channel state cannot be deleted or
rewritten.

The integration test builds the full artifact/approval/authorization chain,
records a valid staging promotion with separate human approver, authorizer,
and operator principals, advances the state from that exact event, and
rejects a mismatched state advance. It also checks event-history and state
deletion protections.

GitHub Actions
[run #1601](https://github.com/Amiroudro/new-sngso/actions/runs/45219851330)
passed:
- PostgreSQL 16 integration suite: **44 passed in 6.68 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

Important boundary: migration 0019 supplies the database event/state guards,
but does not yet provide the application-level single transaction that
reverifies the export, validates authorization and current state, inserts the
staging event, advances state, and appends the global audit event together.
That atomic staging service, concurrency/failure-injection coverage, and
production privilege model remain outstanding.


## Follow-up: final revision, permission, and assignment guards

PostgreSQL migration 0020, `0020_final_revision_and_assignment_guards.sql`,
ports SQLite migration 34. It validates domain-record revision inserts against
the current head, validates permission-grant revision inserts against either
the current head or a legal next status, and guards grant updates so scope and
evidence remain immutable while only legal status transitions advance the
revision. It also validates benchmark assignment revision inserts and enforces
transition receipts, legal status changes, reviewer/curator roles and language,
acceptance deadlines, submitted-review binding, and closure/expiry timing.

PostgreSQL migration 0021, `0021_domain_record_revision_guards.sql`, ports
SQLite migration 35. The actor/reason/timestamp columns were already present
in the PostgreSQL domain table from migration 0003, so no destructive table
rewrite or backfill was needed. Migration 0021 adds identity and provenance
immutability, exact revision increments, monotonic revision timestamps,
article/evidence identity protection, and automatic append-only domain
revision snapshots after updates.

GitHub Actions
[run #1609](https://github.com/Amiroudro/new-sngso/actions/runs/45220799313)
passed:
- PostgreSQL 16 integration suite: **45 passed in 7.93 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

The staging event authority-binding rule from SQLite migration 36 is included
in PostgreSQL migration 0019: nested approval and authorizer identities must
match the immutable decision and release-authorization records. The
application-level atomic staging transaction and production role model remain
open.


## Section 9 migration coverage checkpoint

The PostgreSQL migration set now has a reviewed implementation path for the
full SQLite schema-v36 migration sequence. The later SQLite migration groups
are represented as follows:

| SQLite migrations | PostgreSQL migration(s) |
| --- | --- |
| 1-23 | 0001-0010 |
| 24-25 | 0011 |
| 26 | 0012 |
| 27-28 | 0015-0016 |
| 29-30 | 0017-0018 |
| 31 and 36 | 0019 |
| 32-33 | 0013-0014 |
| 34-35 | 0020-0021 |

The ordering differs because PostgreSQL migrations are grouped by dependency:
pipeline runs precede stage checkpoints and leases, while edition artifact
and authority records precede staging events. PostgreSQL migration 0003
already included the actor/reason/revision timestamp columns that SQLite
migration 35 adds later, so migration 0021 ports their guards and snapshot
behavior without a table rewrite.

This is a migration-coverage checkpoint, not a claim that Section 9 is
finished. A final schema/trigger parity audit is still required. In
particular, there is no complete PostgreSQL runtime registry adapter yet,
and the atomic staging transaction that re-verifies the whole edition,
performs the event/state change, and appends the global audit event together
is still outstanding. Production database role provisioning, least-privilege
tests, concurrency races, failure injection, migration recovery, and the
browser UI's eventual backend integration remain outstanding as well.


## Atomic PostgreSQL staging transaction service

Added `src/sngso_engine/postgres_edition_staging.py` with
`stage_edition_export()`. It requires an expected channel-state revision,
a trusted `AuthenticatedIdentity`, and a mandatory internal export re-verifier.
The service runs through `run_atomic()` and:

1. Locks the staging channel state row and rejects stale expected revisions.
2. Locks and rechecks the stored artifact's canonical payload fingerprint and
   manifest contract.
3. Locks the immutable approval and independent release authorization and
   checks their exact artifact bindings.
4. Locks and rechecks the active human approver, authorizer, and staging
   operator roles; all three authorities must be distinct.
5. Calls the mandatory export re-verifier with the same transaction cursor,
   checks the complete staging-export contract and exact artifact/authority
   bindings, and fingerprints the returned package.
6. Inserts the append-only staging event, advances the channel state using a
   compare-and-swap update, and appends the global audit event before commit.

Promotion rejects an already promoted export. Rollback requires the exact
fingerprint of an earlier promoted export. The staging event and global audit
event use distinct durable IDs. Exceptions from any stage abort the same
transaction.

GitHub Actions
[run #1618](https://github.com/Amiroudro/new-sngso/actions/runs/45222987208)
passed:
- PostgreSQL 16 integration suite: **49 passed in 7.63 seconds**.
- Contract validation: passed.
- Schema-v36 inspection generation and artifact upload: passed.

New integration coverage verifies that a successful operation commits event,
state, and audit head together; an injected audit append failure rolls back
the event and state; stale state is rejected before export re-verification;
and two competing promotions using the same expected revision serialize so
exactly one commits.

**Remaining boundary:** the re-verifier is a required trusted application
callback. It must perform the full PostgreSQL registry-backed revalidation
of the edition, pipeline run, story snapshots, bilingual learning objects,
source endpoints, grants, and permission evidence, and must lock every row it
relies on with `FOR SHARE` (or stronger) for the transaction duration. The
PostgreSQL read adapter that implements those checks has not yet been built
or connected. Therefore this commit establishes and tests the atomic write
boundary, but it is not yet a production-ready end-to-end staging integration.
Identity-provider wiring, database least-privilege provisioning, and
additional rollback-path and adversarial failure tests remain open.


## Staging rollback and PostgreSQL permission-read primitive

The staging service now rejects a no-op rollback to the export already current.
It also has an integration test for two successful promotions followed by a
rollback to the exact previously promoted export. The test verifies the
restored current export, the displaced export as last-known-good, the event
sequence, and all three matching global audit entries.

[CI run #1621](https://github.com/Amiroudro/new-sngso/actions/runs/45223343955)
passed **50 PostgreSQL integration tests in 6.17 seconds**, with contract
validation and schema-v36 inspection/artifact upload passing.

Added `src/sngso_engine/postgres_permission_read.py` with
`evaluate_postgres_grant_for_staging()`. This is a read-only, transaction-bound
rights evaluator. It locks the grant, source, endpoint, grant-to-endpoint
links, grant-to-evidence links, operation-evidence links, and evidence records
using `FOR SHARE`. It validates the grant contract and indexed columns,
requires relational scope tables to match the canonical payload, checks
current source/endpoint status and evidence review status, then applies the
existing fail-closed operation permission policy. It deliberately emits no
separate audit event because it is called within the larger staging
transaction, whose single global audit event records the decision context.

[CI run #1623](https://github.com/Amiroudro/new-sngso/actions/runs/45223664443)
passed **51 PostgreSQL integration tests in 8.67 seconds**, with contract
validation and schema-v36 inspection/artifact upload passing. New rights tests
cover an allowed republication grant, endpoint mismatch, unverified evidence,
and inactive source rejection.

This helper is now available to the trusted staging re-verifier. It does not
yet complete the read adapter: exact PostgreSQL story editorial snapshot
reconstruction/fingerprint parity, bilingual learning-object quality checks,
and pipeline-run/artifact freshness verification still need to be ported and
connected. No staging action is exposed to a live application.


## PostgreSQL edition read adapter: pipeline freshness evidence

Added `src/sngso_engine/postgres_edition_read.py` with a cursor-bound
`PostgresEditionRegistryReadView`. It provides locked, read-only access to
domain records and their revision history, learning objects and revisions,
sources, endpoints, permission grants/evidence, benchmark principals, and
pipeline runs. Exact story-snapshot reconstruction and learning-object quality
reports remain explicit incomplete ports: the adapter raises
`PostgresEditionReadAdapterIncomplete` rather than returning a partial or
misleading result when those exact-parity loaders are absent.

The adapter now also reads every stage checkpoint and its append-only revision
history, and reads the current worker lease with its complete fencing-token
history. It uses the caller's transaction cursor and `FOR SHARE` row locks,
and rejects a checkpoint or lease whose revision history does not terminate
at the current record revision. This supplies the read-side evidence needed
for later pipeline freshness checks; it does not itself declare a pipeline
fresh or authorize staging.

[CI run #1626](https://github.com/Amiroudro/new-sngso/actions/runs/45224233262)
passed **52 PostgreSQL integration tests in 11.00 seconds**.
The pull-request merge-validation run
[#1627](https://github.com/Amiroudro/new-sngso/actions/runs/45224257177)
also passed **52 PostgreSQL integration tests in 6.25 seconds**.
Contract validation passed in both runs.

Remaining read-side work is substantial: implement exact story editorial
snapshot and fingerprint parity, port learning-object quality checks and
bilingual variant verification, then compose these pieces into a single
transaction-bound callback for `stage_edition_export()`. The current adapter
is intentionally incomplete and is not a production staging verifier.


## PostgreSQL edition read view and re-verifier composition

Added `src/sngso_engine/postgres_edition_read.py`, a cursor-bound read-only
facade for the existing edition verifier. It locks current pipeline runs,
pipeline stage checkpoints, worker leases, domain records, learning objects,
source records, endpoints, permission evidence/grants, and human principals
with `FOR SHARE`; it also checks immutable revision heads and verifies
indexed fields against stored payloads. The pipeline checkpoint and worker
lease methods return their full append-only revision histories.

Added `src/sngso_engine/postgres_edition_verifier.py` to compose that view
with `verify_registry_backed_edition_artifact()`. It validates the full
edition staging-export, approval, and release-authorization contracts and
requires exact artifact/authority bindings before returning a package to the
atomic staging service.

[CI run #1636](https://github.com/Amiroudro/new-sngso/actions/runs/45224825334)
passed **53 PostgreSQL integration tests in 6.58 seconds**. Contract
validation and schema-v36 inspection generation/artifact upload also passed.
The new read-view test checks current records and histories, detects an
indexed source/payload mismatch, and verifies checkpoint and worker-lease
revision histories.

**Still deliberately fail-closed:** the adapter requires two exact-parity
loaders that are not yet implemented: (1) PostgreSQL reconstruction of the
SQLite story editorial snapshot and its canonical fingerprint, and (2) the
full PostgreSQL learning-object quality report. The re-verifier factory
refuses to build without both. Therefore the PostgreSQL read path is partly
ported and composed, but end-to-end registry-backed staging remains blocked
until those two editorial-verification ports are complete and tested.


The read adapter's revision checks were tightened after review: pipeline-run,
stage-checkpoint, worker-lease, domain-record, and learning-object histories
must now contain every revision from 1 through the current head. A history
with a matching final revision but missing an intermediate revision is
rejected. Pipeline-run revision snapshots are also checked against the
current payload. The integration fixture now includes the explicit initial
pipeline-run revision record, matching the schema's append-only revision
table contract.

[CI run #1634](https://github.com/Amiroudro/new-sngso/actions/runs/45225012372)
passed **53 PostgreSQL integration tests in 11.21 seconds**. Contract
validation and schema-v36 inspection/artifact upload passed.


The story read surface has also been extended with transaction-bound readers
for timeline-event history, perspective history, claim-relation history, and
story merge/split decisions. They retain superseded records and derive the
same supersession flags as the SQLite registry helpers. These are building
blocks for the full editorial snapshot; reporting-independence and source-
independence report parity, benchmark quality reports, narrative assembly,
and the canonical snapshot fingerprint are still not implemented in
PostgreSQL.

[CI run #1640](https://github.com/Amiroudro/new-sngso/actions/runs/45225308597)
passed **53 PostgreSQL integration tests in 9.46 seconds**. Contract validation
and schema-v36 inspection/artifact upload passed. The integration test now
exercises the transaction-bound story history readers against an active
registered story with empty event, perspective, relation, and decision
histories.


## Initial PostgreSQL story snapshot parity port

Added `src/sngso_engine/postgres_story_snapshot.py`. It reconstructs the
SQLite-compatible story snapshot under shared row locks for stories without
benchmark-linked reviews. It ports the current story/article/claim/evidence
snapshot, timeline, perspective and claim-relation histories, story decisions,
learning objects, glossary entries, ingestion duplicate-review summary,
source-independence caveat, perspective-attribution report, and the existing
pure-Python narrative assembly. The final canonical fingerprint is generated
with the same canonical JSON digest helper.

The read view now uses this builder by default. If a story has benchmark
reviews linked to its articles, it fails closed rather than fabricating an
empty benchmark section. The verifier factory no longer requires an external
story snapshot callback for this supported subset; a learning-object quality
loader is still mandatory.

[CI run #1648](https://github.com/Amiroudro/new-sngso/actions/runs/45226215835)
passed **53 PostgreSQL integration tests in 9.99 seconds**, with contract
validation and schema-v36 inspection generation/artifact upload passing. The
new parity test compares the complete PostgreSQL snapshot and fingerprint
against `SQLiteRegistry.story_editorial_snapshot()` for a minimal story with
a linked draft learning object.

**Remaining snapshot/verification limits:** the parity test does not yet
cover populated article/claim/evidence/timeline/perspective/duplicate-review
graphs. Benchmark-linked stories remain unsupported until the PostgreSQL
benchmark review and acceptance-review quality reports are ported. The
learning-object quality report is still not implemented, so the complete
registry-backed staging re-verifier remains unavailable.


The parity fixture was extended to include a real linked article record as well
as the draft learning object. It now compares the full snapshot and fingerprint
with SQLite for a story with article provenance, publisher-count/duplicate
review reporting, and source-independence output (with no ingestion duplicate
decisions). [CI run #1650](https://github.com/Amiroudro/new-sngso/actions/runs/45226483268)
passed **53 PostgreSQL integration tests in 17.77 seconds**; contract
validation and schema-v36 inspection generation/artifact upload passed.


## PostgreSQL learning-object quality report port

Added `src/sngso_engine/postgres_learning_quality.py`, a read-only port of
`SQLiteRegistry.learning_object_quality_report()`. It evaluates editorial
approval, language-specific review rubrics, MCQ pedagogical review, claim
existence/provenance, claim evidence, factual-segment support, answer claim
support, exact answer-evidence locators/revisions, vocabulary source evidence,
and approved glossary matching. All current learning-object, claim, evidence,
and glossary rows are locked with `FOR SHARE` inside the caller's transaction.

The PostgreSQL read view now uses both the built-in story snapshot builder and
the built-in learning-object quality report by default. The staging re-verifier
factory no longer requires external loader callbacks. Custom loaders remain
available for parity tests. Benchmark-linked stories still fail closed because
the benchmark-review and acceptance-review quality reports have not yet been
ported.

[CI run #1654](https://github.com/Amiroudro/new-sngso/actions/runs/45227156556)
passed **53 PostgreSQL integration tests in 10.14 seconds**, with contract
validation and schema-v36 inspection generation/artifact upload passing. The
new quality parity assertion compares the PostgreSQL report with
`SQLiteRegistry.learning_object_quality_report()` for a draft headline
summary with no claims. This verifies deterministic blocker/check ordering for
that baseline case, not yet the full MCQ, answer-evidence, or vocabulary paths.


The SQLite/PostgreSQL story snapshot and learning-quality parity fixture now
also includes a linked supported claim, its supporting evidence record,
source/article provenance, and a factual summary segment. It compares both
the full story snapshot fingerprint and the full deterministic learning-object
quality report. [CI run #1656](https://github.com/Amiroudro/new-sngso/actions/runs/45227511942)
passed **53 PostgreSQL integration tests in 10.36 seconds**; contract
validation and schema-v36 inspection generation/artifact upload passed.


The learning-quality parity fixture now includes a comprehension question with
an answer claim and an exact current evidence revision/locator. PostgreSQL and
SQLite quality reports match, and the answer-evidence check passes. An
attempted stale-revision fixture was rejected at insert time by the PostgreSQL
database guard itself (`question answer evidence must be current and linked to
its answer claim`); the invalid fixture was removed rather than weakening that
guard. [CI run #1659](https://github.com/Amiroudro/new-sngso/actions/runs/45228007556)
passed **53 PostgreSQL integration tests in 10.70 seconds**, with contract
validation and schema-v36 inspection generation/artifact upload passing.
