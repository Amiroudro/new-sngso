# Section 9 PostgreSQL Schema Inventory

Status: **source-derived migration inventory; review checklist, not a PostgreSQL schema implementation.**

Source: `src/sngso_engine/registry.py` on `feat/section-9-durable-stage-checkpoints` (blob `b1b3e8968531412dcd50a93bcac4c7e9c9b46014`).
Extraction: migration tuple boundaries plus CREATE TABLE, CREATE INDEX, and CREATE TRIGGER declarations. Names are exact source identifiers. SQL bodies, foreign-key actions, CHECK expressions, partial-index predicates, and trigger semantics still require individual review.

## Migration-by-migration inventory

All 35 migration definitions are present and numbered 1–35. Empty object lists are shown explicitly because a migration can add guards or other schema behavior without creating a new table.
### Migration 1

- Tables (12): `schema_migrations`, `sources`, `source_endpoints`, `permission_evidence`, `permission_grants`, `grant_endpoints`, `grant_evidence`, `grant_operation_evidence`, `permission_grant_revisions`, `domain_records`, `domain_record_revisions`, `audit_events`
- Indexes (4): `idx_endpoints_source`, `idx_evidence_source`, `idx_grants_source_status`, `idx_audit_entity`
- Triggers (6): `audit_events_no_update`, `audit_events_no_delete`, `grant_revisions_no_update`, `grant_revisions_no_delete`, `domain_revisions_no_update`, `domain_revisions_no_delete`

### Migration 2

- Tables (1): `collection_observations`
- Indexes (2): `idx_collection_observations_source_time`, `idx_collection_observations_endpoint_time`
- Triggers (2): `collection_observations_no_update`, `collection_observations_no_delete`

### Migration 3

- Tables (0): none
- Indexes (0): none
- Triggers (0): none

### Migration 4

- Tables (2): `ingestion_decisions`, `ingestion_current`
- Indexes (2): `idx_ingestion_fingerprint`, `idx_article_source_uri`
- Triggers (6): `ingestion_decisions_validate_article`, `ingestion_decisions_no_update`, `ingestion_decisions_no_delete`, `ingestion_current_validate`, `ingestion_current_validate_update`, `ingestion_current_no_delete`

### Migration 5

- Tables (1): `story_decisions`
- Indexes (1): `idx_story_decisions_decided_at`
- Triggers (2): `story_decisions_no_update`, `story_decisions_no_delete`

### Migration 6

- Tables (1): `story_decision_applications`
- Indexes (1): `idx_story_decision_applications_applied_at`
- Triggers (2): `story_decision_applications_no_update`, `story_decision_applications_no_delete`

### Migration 7

- Tables (1): `timeline_events`
- Indexes (1): `idx_timeline_events_story_time`
- Triggers (3): `timeline_events_validate_insert`, `timeline_events_no_update`, `timeline_events_no_delete`

### Migration 8

- Tables (1): `perspectives`
- Indexes (1): `idx_perspectives_story_created`
- Triggers (3): `perspectives_validate_insert`, `perspectives_no_update`, `perspectives_no_delete`

### Migration 9

- Tables (1): `claim_relations`
- Indexes (1): `idx_claim_relations_story_created`
- Triggers (3): `claim_relations_validate_insert`, `claim_relations_no_update`, `claim_relations_no_delete`

### Migration 10

- Tables (0): none
- Indexes (0): none
- Triggers (1): `timeline_events_review_validate_insert`

### Migration 11

- Tables (2): `learning_objects`, `learning_object_revisions`
- Indexes (2): `idx_learning_objects_story_kind_language`, `idx_learning_objects_variant_language`
- Triggers (10): `learning_objects_variant_group_validate_insert`, `learning_objects_variant_group_validate_update`, `learning_objects_validate_insert`, `learning_objects_validate_update`, `learning_objects_no_delete`, `learning_object_revisions_validate_insert`, `learning_objects_record_insert`, `learning_objects_record_update`, `learning_object_revisions_no_update`, `learning_object_revisions_no_delete`

### Migration 12

- Tables (2): `glossary_entries`, `glossary_entry_revisions`
- Indexes (0): none
- Triggers (12): `glossary_entries_validate_insert`, `glossary_entries_validate_update`, `glossary_entries_no_delete`, `glossary_entries_record_insert`, `glossary_entries_record_update`, `glossary_entry_revisions_validate_insert`, `glossary_entry_revisions_no_update`, `glossary_entry_revisions_no_delete`, `learning_objects_mcq_validate_insert`, `learning_objects_mcq_validate_update`, `learning_objects_glossary_validate_insert`, `learning_objects_glossary_validate_update`

### Migration 13

- Tables (2): `benchmark_reviews`, `benchmark_review_revisions`
- Indexes (1): `idx_benchmark_reviews_benchmark`
- Triggers (8): `benchmark_reviews_validate_insert`, `benchmark_reviews_validate_update`, `benchmark_reviews_no_delete`, `benchmark_reviews_record_insert`, `benchmark_reviews_record_update`, `benchmark_review_revisions_validate_insert`, `benchmark_review_revisions_no_update`, `benchmark_review_revisions_no_delete`

### Migration 14

- Tables (1): `benchmark_artifacts`
- Indexes (0): none
- Triggers (2): `benchmark_artifacts_no_update`, `benchmark_artifacts_no_delete`

### Migration 15

- Tables (2): `benchmark_principals`, `benchmark_adjudications`
- Indexes (0): none
- Triggers (2): `benchmark_adjudications_no_update`, `benchmark_adjudications_no_delete`

### Migration 16

- Tables (0): none
- Indexes (0): none
- Triggers (1): `benchmark_adjudications_one_per_artifact`

### Migration 17

- Tables (2): `benchmark_review_assignments`, `benchmark_review_assignment_revisions`
- Indexes (2): `benchmark_review_assignment_one_active_per_reviewer`, `benchmark_review_assignment_reviewer_status`
- Triggers (8): `benchmark_review_assignment_revision_guard`, `benchmark_review_assignment_payload_guard_insert`, `benchmark_review_assignment_payload_guard_update`, `benchmark_review_assignments_no_delete`, `benchmark_review_assignment_initial_revision`, `benchmark_review_assignment_update_requires_revision`, `benchmark_review_assignment_revisions_no_update`, `benchmark_review_assignment_revisions_no_delete`

### Migration 18

- Tables (0): none
- Indexes (0): none
- Triggers (1): `benchmark_principal_kind_immutable`

### Migration 19

- Tables (1): `benchmark_review_identity_attestations`
- Indexes (0): none
- Triggers (2): `benchmark_review_identity_attestations_no_update`, `benchmark_review_identity_attestations_no_delete`

### Migration 20

- Tables (1): `benchmark_scores`
- Indexes (1): `benchmark_scores_artifact_item`
- Triggers (3): `benchmark_scores_payload_guard_insert`, `benchmark_scores_no_update`, `benchmark_scores_no_delete`

### Migration 21

- Tables (1): `benchmark_study_protocols`
- Indexes (1): `benchmark_study_protocols_benchmark`
- Triggers (4): `benchmark_study_protocols_payload_guard`, `benchmark_study_protocols_version_guard`, `benchmark_study_protocols_no_update`, `benchmark_study_protocols_no_delete`

### Migration 22

- Tables (1): `benchmark_study_executions`
- Indexes (1): `benchmark_study_executions_study`
- Triggers (3): `benchmark_study_executions_payload_guard`, `benchmark_study_executions_no_update`, `benchmark_study_executions_no_delete`

### Migration 23

- Tables (1): `benchmark_acceptance_reviews`
- Indexes (1): `benchmark_acceptance_reviews_study`
- Triggers (3): `benchmark_acceptance_reviews_payload_guard`, `benchmark_acceptance_reviews_no_update`, `benchmark_acceptance_reviews_no_delete`

### Migration 24

- Tables (0): none
- Indexes (0): none
- Triggers (2): `learning_object_vocabulary_source_evidence_insert`, `learning_object_vocabulary_source_evidence_update`

### Migration 25

- Tables (0): none
- Indexes (0): none
- Triggers (2): `learning_object_question_answer_evidence_insert`, `learning_object_question_answer_evidence_update`

### Migration 26

- Tables (2): `pipeline_runs`, `pipeline_run_revisions`
- Indexes (1): `pipeline_runs_status_started`
- Triggers (7): `pipeline_run_payload_guard_insert`, `pipeline_run_payload_guard_update`, `pipeline_run_identity_guard`, `pipeline_runs_no_delete`, `pipeline_run_revision_no_update`, `pipeline_run_revision_no_delete`, `pipeline_run_revision_matches_current`

### Migration 27

- Tables (1): `edition_artifacts`
- Indexes (0): none
- Triggers (3): `edition_artifacts_payload_guard`, `edition_artifacts_no_update`, `edition_artifacts_no_delete`

### Migration 28

- Tables (1): `edition_decisions`
- Indexes (0): none
- Triggers (3): `edition_decisions_payload_guard`, `edition_decisions_no_update`, `edition_decisions_no_delete`

### Migration 29

- Tables (0): none
- Indexes (0): none
- Triggers (3): `edition_domain_records_no_published_insert`, `edition_domain_records_no_update`, `edition_domain_records_no_delete`

### Migration 30

- Tables (1): `edition_release_authorizations`
- Indexes (0): none
- Triggers (3): `edition_release_authorizations_insert_guard`, `edition_release_authorizations_no_update`, `edition_release_authorizations_no_delete`

### Migration 31

- Tables (2): `edition_staging_events`, `edition_staging_channel_state`
- Indexes (0): none
- Triggers (5): `edition_staging_events_insert_guard`, `edition_staging_events_no_update`, `edition_staging_events_no_delete`, `edition_staging_state_update_guard`, `edition_staging_state_no_delete`

### Migration 32

- Tables (2): `pipeline_stage_checkpoints`, `pipeline_stage_checkpoint_revisions`
- Indexes (1): `pipeline_stage_checkpoints_status`
- Triggers (9): `pipeline_stage_checkpoint_initial_revision`, `pipeline_stage_checkpoint_revision_guard`, `pipeline_stage_checkpoint_revision_snapshot`, `pipeline_stage_checkpoint_metadata_guard_insert`, `pipeline_stage_checkpoint_metadata_guard_update`, `pipeline_stage_checkpoint_revision_matches_head`, `pipeline_stage_checkpoint_revisions_no_update`, `pipeline_stage_checkpoint_revisions_no_delete`, `pipeline_stage_checkpoints_no_delete`

### Migration 34

- Tables (2): `pipeline_worker_leases`, `pipeline_worker_lease_revisions`
- Indexes (1): `pipeline_worker_leases_expiry`
- Triggers (7): `pipeline_worker_lease_initial_revision`, `pipeline_worker_lease_revision_guard`, `pipeline_worker_lease_revision_snapshot`, `pipeline_worker_lease_revision_matches_head`, `pipeline_worker_lease_revisions_no_update`, `pipeline_worker_lease_revisions_no_delete`, `pipeline_worker_leases_no_delete`

## Inventory totals

| Object type | Count |
|---|---:|
| Migration definitions | 34 |
| Distinct table definitions | 47 |
| Distinct index definitions | 25 |
| Distinct trigger definitions | 139 |

## Porting rules

1. Preserve migration ordering and upgrade semantics; do not simply create the latest schema and assume historical data is compatible.
2. For each table, port column types, nullability, defaults, primary/unique/foreign keys, CHECK constraints, and ON DELETE/UPDATE behavior.
3. For each index, preserve uniqueness, column order, collation, and any WHERE predicate.
4. For each trigger, record the guarded event, timing, affected columns, referenced tables, error behavior, and whether it enforces immutability, revision integrity, payload consistency, or cross-record relationships.
5. Add a test for each invariant, including a negative test that attempts to violate it through direct SQL using the application runtime role.
6. Keep canonical JSON bytes and existing audit hashes stable; PostgreSQL JSONB text output must not silently replace the current canonical serializer.

## Scope warning

This inventory does not prove that every SQL object is represented by a simple declaration regex in future source versions. Re-run extraction after every registry migration and compare it with SQLite `sqlite_master` on a real schema-v36 fixture. The live fixture, `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and audit-chain verification remain mandatory.
