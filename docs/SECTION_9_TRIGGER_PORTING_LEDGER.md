# Section 9 Trigger Porting Ledger

Status: **live SQLite trigger inventory captured; semantic PostgreSQL port review not yet complete.**

Evidence source: CI-generated schema-v33 inspection artifact from [GitHub Actions run #45054352627](https://github.com/Amiroudro/new-sngso/actions/runs/45054352627). The artifact contains the exact SQL definition for every trigger from SQLite `sqlite_master`; it is retained until 6 December 2026.

## Live trigger event distribution

The freshly migrated schema contains **131 triggers across 39 tables**.

| SQLite event timing | Trigger count |
|---|---:|
| BEFORE INSERT | 34 |
| BEFORE UPDATE | 45 |
| BEFORE UPDATE OF payload_json | 2 |
| BEFORE UPDATE OF principal_kind | 1 |
| BEFORE DELETE | 38 |
| AFTER INSERT | 6 |
| AFTER UPDATE | 5 |
| **Total** | **131** |

**54** triggers include a `WHEN` predicate. The count above is extracted from the live SQL definitions; it does not imply that a trigger has been semantically reviewed or ported.

## Trigger coverage by table

| Table | Trigger count |
|---|---:|
| audit_events | 2 |
| benchmark_acceptance_reviews | 3 |
| benchmark_adjudications | 3 |
| benchmark_artifacts | 2 |
| benchmark_principals | 1 |
| benchmark_review_assignment_revisions | 2 |
| benchmark_review_assignments | 6 |
| benchmark_review_identity_attestations | 2 |
| benchmark_review_revisions | 3 |
| benchmark_reviews | 5 |
| benchmark_scores | 3 |
| benchmark_study_executions | 3 |
| benchmark_study_protocols | 4 |
| claim_relations | 3 |
| collection_observations | 2 |
| domain_record_revisions | 2 |
| domain_records | 3 |
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
| permission_grant_revisions | 2 |
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
| **Total** | **131** |

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
