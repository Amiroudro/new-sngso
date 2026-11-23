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
