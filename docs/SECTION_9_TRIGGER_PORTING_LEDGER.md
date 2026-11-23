# Section 9 Trigger Porting Ledger

Status: **the schema-v35 ledger below is retained as historical evidence; schema v36 is current with 139 source-level reviewed SQLite triggers. PostgreSQL migrations 0001–0023 now implement substantial coverage across the registry, operational pipeline, benchmark, and staging families. A complete one-to-one trigger mapping, adversarial direct-SQL conformance under a restricted runtime role, and full 139-trigger parity acceptance remain open.**

Historical evidence source: CI-generated schema-v35 inspection artifact from [GitHub Actions run #45057281125](https://github.com/Amiroudro/new-sngso/actions/runs/45057281125). The artifact contains the exact SQL definition for every trigger from SQLite `sqlite_master`; it is retained until 6 December 2026.

## Live trigger event distribution

The freshly migrated schema contains **138 triggers across 40 tables**.

| SQLite event timing | Trigger count |
|---|---:|
| BEFORE INSERT | 37 |
| BEFORE UPDATE | 48 |
| BEFORE UPDATE OF payload_json | 2 |
| BEFORE UPDATE OF principal_kind | 1 |
| BEFORE DELETE | 38 |
| AFTER INSERT | 6 |
| AFTER UPDATE | 6 |
| **Total** | **138** |

**54** triggers include a `WHEN` predicate. The count above is extracted from the live SQL definitions. All 138 triggers now have source-level review records in the linked family reviews; this does not mean they have been ported or that all direct-SQL tests are complete.

## Trigger coverage by table

| Table | Trigger count |
|---|---:|
| audit_events | 2 |
| benchmark_acceptance_reviews | 3 |
| benchmark_adjudications | 3 |
| benchmark_artifacts | 2 |
| benchmark_principals | 1 |
| benchmark_review_assignment_revisions | 3 |
| benchmark_review_assignments | 7 |
| benchmark_review_identity_attestations | 2 |
| benchmark_review_revisions | 3 |
| benchmark_reviews | 5 |
| benchmark_scores | 3 |
| benchmark_study_executions | 3 |
| benchmark_study_protocols | 4 |
| claim_relations | 3 |
| collection_observations | 2 |
| domain_record_revisions | 3 |
| domain_records | 5 |
| edition_artifacts | 3 |
| edition_decisions | 3 |
| edition_release_authorizations | 3 |
| edition_staging_channel_state | 2 |
| edition_staging_events | 3 |
| glossary_entries | 5 |
| glossary_entry_revisions | 3 |
| ingestion_current | 3 |
| ingestion_decisions | 3 |
| learning_object_revisions | 3 |
| learning_objects | 15 |
| permission_grant_revisions | 3 |
| perspectives | 3 |
| pipeline_run_revisions | 3 |
| pipeline_runs | 4 |
| pipeline_stage_checkpoint_revisions | 3 |
| pipeline_stage_checkpoints | 6 |
| pipeline_worker_lease_revisions | 3 |
| pipeline_worker_leases | 4 |
| story_decision_applications | 2 |
| story_decisions | 2 |
| timeline_events | 4 |
| **Total** | **138** |

## Required semantic review record

For every trigger, the PostgreSQL port must record:

1. Exact trigger name and source table.
2. BEFORE/AFTER timing and INSERT/UPDATE/DELETE event, including restricted UPDATE OF columns.
3. Full WHEN predicate and all referenced JSON paths.
4. Every table read or written by the trigger.
5. Whether it protects immutability, append-only history, revision sequencing, head/revision equivalence, payload/index consistency, cross-record references, publication gates, or audit integrity.
6. Exact abort/error condition and expected application error mapping.
7. Whether PostgreSQL enforcement belongs in a CHECK/UNIQUE/FOREIGN KEY constraint, a trigger, a stored procedure, or transaction-manager logic.
8. Positive and negative direct-SQL tests executed using the production application role.
9. Concurrency behavior under independent PostgreSQL connections.
10. Reviewer, review date, implementation commit, and test evidence.

## High-risk trigger families

Prioritize these groups during port design:

- **Audit chain:** `audit_events_no_update`, `audit_events_no_delete`, plus the registry's globally serialized append operation.
- **Revision-backed heads:** benchmark review assignments, benchmark reviews, glossary entries, learning objects, pipeline runs, checkpoints, and worker leases.
- **Operational state machines:** `pipeline_run_identity_guard`, checkpoint metadata/revision guards, and worker lease transition/fencing guards.
- **Publication boundary:** edition domain-record guards, release authorization guards, staging event guards, and staging channel-state guards.
- **Evidence and editorial integrity:** benchmark payload guards, human-review records, source evidence requirements, claim/story relationships, and learning-object evidence guards.

These are prioritization labels, not completed semantic reviews. The authoritative SQLite SQL remains the CI artifact. A PostgreSQL implementation must preserve behavior, not merely reuse trigger names.

## Acceptance rule

This ledger closes the **live inventory capture** portion of P0. It does not close trigger semantics review or authorize a PostgreSQL migration. Each trigger must have a reviewed implementation mapping and executable negative tests before the PostgreSQL schema is accepted.


## Schema-v36 delta

Migration 36 adds one BEFORE INSERT trigger, `edition_staging_events_authority_binding_guard`, bringing the live schema to **139 triggers**. The new trigger binds payload-declared approver and authorizer identities to the immutable linked decision and release-authorization rows. The schema-v36 CI artifact `14818506827` from run #1480 is the current live-schema evidence. The 138-trigger tables above remain an explicitly historical schema-v35 snapshot; regenerate the complete lexical ledger from the v36 artifact before PostgreSQL DDL implementation.


## PostgreSQL implementation checkpoint (2026-11-24)

Draft PR #8 now contains PostgreSQL migrations 0001–0022. The migration
families cover source permissions and collection/ingestion, story/editorial
history, learning and glossary lifecycle/evidence, benchmark governance and
study evidence, pipeline runs/checkpoints/worker leases, edition artifacts
and decisions, release authorization, staging events/state, and late
revision/assignment guards. Migration 0022 adds transaction-held shared locks
for specialized learning evidence validation.

The PostgreSQL implementation also has cursor-bound read and quality ports,
including benchmark review/acceptance quality reports and linked story
snapshot inclusion. Atomic staging and global audit append share one
transaction, and the staging integration tests exercise rollback and
competing operations.

Verified evidence:
- [CI #45240658878](https://github.com/Amiroudro/new-sngso/actions/runs/45240658878):
  **66 PostgreSQL integration tests passed**; contract validation passed.
- Earlier [CI #45235178227](https://github.com/Amiroudro/new-sngso/actions/runs/45235178227):
  58 PostgreSQL integration tests passed, including benchmark readiness and
  evaluator-agreement read-port tests.

These tests execute against fresh isolated PostgreSQL schemas. They do not
yet prove an imported schema-v36 dataset, a complete one-to-one semantic
mapping of all 139 SQLite triggers, every negative direct-SQL case under the
deployment runtime role, or production topology/restore/failover behavior.
