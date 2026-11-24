-- PostgreSQL migration 0024: complete the source schema's append-only DELETE guards.
-- These guard relations whose SQLite contract rejects every row deletion.

CREATE FUNCTION reject_registry_row_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'registry rows are append-only'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER benchmark_artifacts_no_delete
BEFORE DELETE ON benchmark_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_adjudications_no_delete
BEFORE DELETE ON benchmark_adjudications
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_review_assignment_revisions_no_delete
BEFORE DELETE ON benchmark_review_assignment_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_review_identity_attestations_no_delete
BEFORE DELETE ON benchmark_review_identity_attestations
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_scores_no_delete
BEFORE DELETE ON benchmark_scores
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_study_protocols_no_delete
BEFORE DELETE ON benchmark_study_protocols
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_study_executions_no_delete
BEFORE DELETE ON benchmark_study_executions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_acceptance_reviews_no_delete
BEFORE DELETE ON benchmark_acceptance_reviews
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER pipeline_run_revision_no_delete
BEFORE DELETE ON pipeline_run_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER edition_artifacts_no_delete
BEFORE DELETE ON edition_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER edition_decisions_no_delete
BEFORE DELETE ON edition_decisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER edition_release_authorizations_no_delete
BEFORE DELETE ON edition_release_authorizations
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER edition_staging_events_no_delete
BEFORE DELETE ON edition_staging_events
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER grant_revisions_no_delete
BEFORE DELETE ON permission_grant_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER domain_revisions_no_delete
BEFORE DELETE ON domain_record_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER collection_observations_no_delete
BEFORE DELETE ON collection_observations
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER ingestion_decisions_no_delete
BEFORE DELETE ON ingestion_decisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER story_decisions_no_delete
BEFORE DELETE ON story_decisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER story_decision_applications_no_delete
BEFORE DELETE ON story_decision_applications
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER timeline_events_no_delete
BEFORE DELETE ON timeline_events
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER perspectives_no_delete
BEFORE DELETE ON perspectives
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER claim_relations_no_delete
BEFORE DELETE ON claim_relations
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER learning_object_revisions_no_delete
BEFORE DELETE ON learning_object_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER glossary_entry_revisions_no_delete
BEFORE DELETE ON glossary_entry_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER benchmark_review_revisions_no_delete
BEFORE DELETE ON benchmark_review_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER pipeline_stage_checkpoint_revisions_no_delete
BEFORE DELETE ON pipeline_stage_checkpoint_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

CREATE TRIGGER pipeline_worker_lease_revisions_no_delete
BEFORE DELETE ON pipeline_worker_lease_revisions
FOR EACH ROW EXECUTE FUNCTION reject_registry_row_delete();

