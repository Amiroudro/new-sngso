-- PostgreSQL migration 0009: benchmark review assignments, identity attestations and scores.
-- Ports SQLite migrations 17, 19 and 20. Principal language/classification
-- columns were intentionally included in PostgreSQL migration 0008.

CREATE TABLE benchmark_review_assignments (
    assignment_id text PRIMARY KEY CHECK (length(btrim(assignment_id)) > 0),
    artifact_id text NOT NULL REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
    artifact_fingerprint text NOT NULL CHECK (artifact_fingerprint LIKE 'sha256:%'),
    language text NOT NULL CHECK (language IN ('bn', 'en')),
    reviewer_id text NOT NULL REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    assigned_by text NOT NULL REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    assigned_at text NOT NULL,
    due_at text NOT NULL,
    status text NOT NULL CHECK (status IN ('assigned', 'accepted', 'submitted', 'cancelled', 'expired')),
    revision integer NOT NULL CHECK (revision >= 1),
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    updated_at text NOT NULL,
    last_actor text NOT NULL CHECK (length(btrim(last_actor)) > 0),
    last_reason text NOT NULL CHECK (length(btrim(last_reason)) > 0)
);

CREATE UNIQUE INDEX benchmark_review_assignment_one_active_per_reviewer
    ON benchmark_review_assignments(artifact_id, reviewer_id)
    WHERE status IN ('assigned', 'accepted');
CREATE INDEX benchmark_review_assignment_reviewer_status
    ON benchmark_review_assignments(reviewer_id, status, due_at);

CREATE TABLE benchmark_review_assignment_revisions (
    assignment_id text NOT NULL REFERENCES benchmark_review_assignments(assignment_id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    PRIMARY KEY (assignment_id, revision)
);

CREATE FUNCTION validate_benchmark_review_assignment_write()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload_json::jsonb ->> 'assignment_id' IS DISTINCT FROM NEW.assignment_id
       OR NEW.payload_json::jsonb ->> 'artifact_id' IS DISTINCT FROM NEW.artifact_id
       OR NEW.payload_json::jsonb ->> 'artifact_fingerprint' IS DISTINCT FROM NEW.artifact_fingerprint
       OR NEW.payload_json::jsonb ->> 'language' IS DISTINCT FROM NEW.language
       OR NEW.payload_json::jsonb ->> 'reviewer_id' IS DISTINCT FROM NEW.reviewer_id
       OR NEW.payload_json::jsonb ->> 'assigned_by' IS DISTINCT FROM NEW.assigned_by
       OR NEW.payload_json::jsonb ->> 'status' IS DISTINCT FROM NEW.status THEN
        RAISE EXCEPTION 'assignment payload does not match indexed fields'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> 1 THEN
            RAISE EXCEPTION 'assignment must start at revision one'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
           OR NEW.artifact_id IS DISTINCT FROM OLD.artifact_id
           OR NEW.artifact_fingerprint IS DISTINCT FROM OLD.artifact_fingerprint
           OR NEW.language IS DISTINCT FROM OLD.language
           OR NEW.reviewer_id IS DISTINCT FROM OLD.reviewer_id
           OR NEW.assigned_by IS DISTINCT FROM OLD.assigned_by
           OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
           OR NEW.due_at IS DISTINCT FROM OLD.due_at THEN
            RAISE EXCEPTION 'assignment identity and artifact binding are immutable'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'assignment revisions must increment sequentially'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.updated_at::timestamptz < OLD.updated_at::timestamptz THEN
            RAISE EXCEPTION 'assignment update time cannot move backwards'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_review_assignments_validate_write
BEFORE INSERT OR UPDATE ON benchmark_review_assignments
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_review_assignment_write();

CREATE FUNCTION record_benchmark_review_assignment_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO benchmark_review_assignment_revisions
        (assignment_id, revision, created_at, actor, reason, payload_json)
    VALUES (
        NEW.assignment_id, NEW.revision,
        CASE WHEN TG_OP = 'INSERT' THEN NEW.assigned_at ELSE NEW.updated_at END,
        NEW.last_actor, NEW.last_reason, NEW.payload_json
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_review_assignments_record_revision
AFTER INSERT OR UPDATE ON benchmark_review_assignments
FOR EACH ROW EXECUTE FUNCTION record_benchmark_review_assignment_revision();

CREATE FUNCTION reject_benchmark_review_assignment_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark review assignments cannot be deleted'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_review_assignments_no_delete
BEFORE DELETE ON benchmark_review_assignments
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_review_assignment_delete();

CREATE FUNCTION reject_benchmark_review_assignment_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark review assignment revisions are append-only'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_review_assignment_revisions_no_update
BEFORE UPDATE OR DELETE ON benchmark_review_assignment_revisions
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_review_assignment_revision_mutation();

CREATE TABLE benchmark_review_identity_attestations (
    attestation_id text PRIMARY KEY CHECK (length(btrim(attestation_id)) > 0),
    review_id text NOT NULL REFERENCES benchmark_reviews(review_id) ON DELETE RESTRICT,
    review_revision integer NOT NULL CHECK (review_revision >= 1),
    principal_id text NOT NULL REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    issuer text NOT NULL CHECK (length(btrim(issuer)) > 0),
    audience text NOT NULL CHECK (length(btrim(audience)) > 0),
    subject text NOT NULL CHECK (length(btrim(subject)) > 0),
    authenticated_at text NOT NULL,
    expires_at text NOT NULL,
    authentication_methods_json text NOT NULL
        CHECK (jsonb_typeof(authentication_methods_json::jsonb) = 'array'),
    authentication_context text NOT NULL CHECK (length(btrim(authentication_context)) > 0),
    provider_key_id text NOT NULL CHECK (length(btrim(provider_key_id)) > 0),
    provider_adapter text NOT NULL CHECK (length(btrim(provider_adapter)) > 0),
    session_fingerprint text NOT NULL CHECK (session_fingerprint LIKE 'sha256:%'),
    attested_at text NOT NULL,
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    UNIQUE (review_id, review_revision)
);

CREATE FUNCTION reject_benchmark_review_identity_attestation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'identity attestations are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_review_identity_attestations_no_update
BEFORE UPDATE OR DELETE ON benchmark_review_identity_attestations
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_review_identity_attestation_mutation();

CREATE TABLE benchmark_scores (
    score_id text PRIMARY KEY CHECK (length(btrim(score_id)) > 0),
    assignment_id text NOT NULL REFERENCES benchmark_review_assignments(assignment_id) ON DELETE RESTRICT,
    artifact_id text NOT NULL REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
    artifact_fingerprint text NOT NULL CHECK (artifact_fingerprint LIKE 'sha256:%'),
    item_id text NOT NULL,
    task_type text NOT NULL,
    language text NOT NULL CHECK (language IN ('bn', 'en')),
    evaluator_id text NOT NULL REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    evaluated_at text NOT NULL,
    weighted_score text NOT NULL,
    eligible_for_comparison integer NOT NULL CHECK (eligible_for_comparison IN (0, 1)),
    score_fingerprint text NOT NULL CHECK (score_fingerprint LIKE 'sha256:%'),
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    issuer text NOT NULL,
    subject text NOT NULL,
    provider_key_id text NOT NULL,
    provider_adapter text NOT NULL,
    authenticated_at text NOT NULL,
    created_at text NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    UNIQUE (assignment_id, item_id)
);
CREATE INDEX benchmark_scores_artifact_item
    ON benchmark_scores(artifact_id, item_id, language);

CREATE FUNCTION validate_benchmark_score_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload_json::jsonb ->> 'benchmark_id' IS DISTINCT FROM NEW.artifact_id
       OR NEW.payload_json::jsonb ->> 'benchmark_fingerprint' IS DISTINCT FROM NEW.artifact_fingerprint
       OR NEW.payload_json::jsonb ->> 'item_id' IS DISTINCT FROM NEW.item_id
       OR NEW.payload_json::jsonb ->> 'task_type' IS DISTINCT FROM NEW.task_type
       OR NEW.payload_json::jsonb ->> 'language' IS DISTINCT FROM NEW.language
       OR NEW.payload_json::jsonb ->> 'evaluator_id' IS DISTINCT FROM NEW.evaluator_id
       OR NEW.payload_json::jsonb ->> 'evaluated_at' IS DISTINCT FROM NEW.evaluated_at THEN
        RAISE EXCEPTION 'score payload does not match indexed score fields'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_scores_payload_guard_insert
BEFORE INSERT ON benchmark_scores
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_score_payload();

CREATE FUNCTION reject_benchmark_score_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark score records are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_scores_no_update
BEFORE UPDATE OR DELETE ON benchmark_scores
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_score_mutation();
