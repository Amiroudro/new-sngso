-- PostgreSQL migration 0010: immutable benchmark study protocol and review evidence.
-- Ports SQLite migrations 21-23.

CREATE TABLE benchmark_study_protocols (
    protocol_record_id text PRIMARY KEY CHECK (length(btrim(protocol_record_id)) > 0),
    study_id text NOT NULL CHECK (length(btrim(study_id)) > 0),
    study_version integer NOT NULL CHECK (study_version >= 1),
    benchmark_id text NOT NULL REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
    benchmark_fingerprint text NOT NULL CHECK (benchmark_fingerprint LIKE 'sha256:%'),
    rubric_version text NOT NULL CHECK (length(btrim(rubric_version)) > 0),
    protocol_fingerprint text NOT NULL UNIQUE CHECK (protocol_fingerprint LIKE 'sha256:%'),
    status text NOT NULL CHECK (status = 'protocol_only'),
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    UNIQUE (study_id, study_version)
);
CREATE INDEX benchmark_study_protocols_benchmark
    ON benchmark_study_protocols(benchmark_id, study_id, study_version);

CREATE FUNCTION validate_benchmark_study_protocol_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload_json::jsonb ->> 'study_id' IS DISTINCT FROM NEW.study_id
       OR NEW.payload_json::jsonb ->> 'study_version' IS DISTINCT FROM NEW.study_version::text
       OR NEW.payload_json::jsonb ->> 'benchmark_id' IS DISTINCT FROM NEW.benchmark_id
       OR NEW.payload_json::jsonb ->> 'benchmark_fingerprint' IS DISTINCT FROM NEW.benchmark_fingerprint
       OR NEW.payload_json::jsonb ->> 'rubric_version' IS DISTINCT FROM NEW.rubric_version
       OR NEW.payload_json::jsonb ->> 'status' IS DISTINCT FROM NEW.status THEN
        RAISE EXCEPTION 'study protocol payload does not match indexed fields'
            USING ERRCODE = '23514';
    END IF;

    IF (NEW.study_version = 1 AND EXISTS (
            SELECT 1 FROM benchmark_study_protocols WHERE study_id = NEW.study_id
        ))
       OR (NEW.study_version > 1 AND NOT EXISTS (
            SELECT 1 FROM benchmark_study_protocols
             WHERE study_id = NEW.study_id
               AND study_version = NEW.study_version - 1
               AND benchmark_id = NEW.benchmark_id
               AND benchmark_fingerprint = NEW.benchmark_fingerprint
               AND rubric_version = NEW.rubric_version
        )) THEN
        RAISE EXCEPTION 'study protocol versions must be sequential and preserve benchmark/rubric binding'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_study_protocols_validate_insert
BEFORE INSERT ON benchmark_study_protocols
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_study_protocol_insert();

CREATE FUNCTION reject_benchmark_study_protocol_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'study protocol versions are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_study_protocols_no_update
BEFORE UPDATE OR DELETE ON benchmark_study_protocols
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_study_protocol_mutation();

CREATE TABLE benchmark_study_executions (
    execution_id text PRIMARY KEY CHECK (length(btrim(execution_id)) > 0),
    protocol_record_id text NOT NULL REFERENCES benchmark_study_protocols(protocol_record_id) ON DELETE RESTRICT,
    study_id text NOT NULL,
    study_version integer NOT NULL CHECK (study_version >= 1),
    protocol_fingerprint text NOT NULL CHECK (protocol_fingerprint LIKE 'sha256:%'),
    benchmark_id text NOT NULL REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
    benchmark_fingerprint text NOT NULL CHECK (benchmark_fingerprint LIKE 'sha256:%'),
    rubric_version text NOT NULL,
    execution_fingerprint text NOT NULL UNIQUE CHECK (execution_fingerprint LIKE 'sha256:%'),
    status text NOT NULL CHECK (status = 'analysis_reviewed'),
    methodologist_id text NOT NULL REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    issuer text NOT NULL CHECK (length(btrim(issuer)) > 0),
    subject text NOT NULL CHECK (length(btrim(subject)) > 0),
    provider_key_id text NOT NULL CHECK (length(btrim(provider_key_id)) > 0),
    provider_adapter text NOT NULL CHECK (length(btrim(provider_adapter)) > 0),
    authenticated_at text NOT NULL,
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0)
);
CREATE INDEX benchmark_study_executions_study
    ON benchmark_study_executions(study_id, study_version, created_at);

CREATE FUNCTION validate_benchmark_study_execution_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload_json::jsonb ->> 'execution_id' IS DISTINCT FROM NEW.execution_id
       OR NEW.payload_json::jsonb ->> 'study_id' IS DISTINCT FROM NEW.study_id
       OR NEW.payload_json::jsonb ->> 'study_version' IS DISTINCT FROM NEW.study_version::text
       OR NEW.payload_json::jsonb ->> 'protocol_fingerprint' IS DISTINCT FROM NEW.protocol_fingerprint
       OR NEW.payload_json::jsonb ->> 'benchmark_id' IS DISTINCT FROM NEW.benchmark_id
       OR NEW.payload_json::jsonb ->> 'benchmark_fingerprint' IS DISTINCT FROM NEW.benchmark_fingerprint
       OR NEW.payload_json::jsonb ->> 'rubric_version' IS DISTINCT FROM NEW.rubric_version
       OR NEW.payload_json::jsonb #>> '{analysis,methodologist_id}' IS DISTINCT FROM NEW.methodologist_id THEN
        RAISE EXCEPTION 'study execution payload does not match indexed fields'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_study_executions_validate_insert
BEFORE INSERT ON benchmark_study_executions
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_study_execution_insert();

CREATE FUNCTION reject_benchmark_study_execution_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'study execution records are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_study_executions_no_update
BEFORE UPDATE OR DELETE ON benchmark_study_executions
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_study_execution_mutation();

CREATE TABLE benchmark_acceptance_reviews (
    review_id text PRIMARY KEY CHECK (length(btrim(review_id)) > 0),
    execution_id text NOT NULL UNIQUE REFERENCES benchmark_study_executions(execution_id) ON DELETE RESTRICT,
    protocol_record_id text NOT NULL REFERENCES benchmark_study_protocols(protocol_record_id) ON DELETE RESTRICT,
    study_id text NOT NULL,
    study_version integer NOT NULL CHECK (study_version >= 1),
    protocol_fingerprint text NOT NULL CHECK (protocol_fingerprint LIKE 'sha256:%'),
    execution_fingerprint text NOT NULL CHECK (execution_fingerprint LIKE 'sha256:%'),
    acceptance_fingerprint text NOT NULL UNIQUE CHECK (acceptance_fingerprint LIKE 'sha256:%'),
    benchmark_id text NOT NULL REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
    benchmark_fingerprint text NOT NULL CHECK (benchmark_fingerprint LIKE 'sha256:%'),
    rubric_version text NOT NULL,
    reviewer_id text NOT NULL REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    reviewer_role text NOT NULL CHECK (reviewer_role = 'independent_acceptance_reviewer'),
    decision text NOT NULL CHECK (decision IN (
        'not_reviewed', 'needs_revision', 'rejected',
        'accepted_for_limited_use', 'accepted_for_proposed_use'
    )),
    issuer text NOT NULL CHECK (length(btrim(issuer)) > 0),
    subject text NOT NULL CHECK (length(btrim(subject)) > 0),
    provider_key_id text NOT NULL CHECK (length(btrim(provider_key_id)) > 0),
    provider_adapter text NOT NULL CHECK (length(btrim(provider_adapter)) > 0),
    authenticated_at text NOT NULL,
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0)
);
CREATE INDEX benchmark_acceptance_reviews_study
    ON benchmark_acceptance_reviews(study_id, study_version, created_at);

CREATE FUNCTION validate_benchmark_acceptance_review_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload_json::jsonb ->> 'review_id' IS DISTINCT FROM NEW.review_id
       OR NEW.payload_json::jsonb ->> 'execution_id' IS DISTINCT FROM NEW.execution_id
       OR NEW.payload_json::jsonb ->> 'study_id' IS DISTINCT FROM NEW.study_id
       OR NEW.payload_json::jsonb ->> 'study_version' IS DISTINCT FROM NEW.study_version::text
       OR NEW.payload_json::jsonb ->> 'protocol_fingerprint' IS DISTINCT FROM NEW.protocol_fingerprint
       OR NEW.payload_json::jsonb ->> 'execution_fingerprint' IS DISTINCT FROM NEW.execution_fingerprint
       OR NEW.payload_json::jsonb ->> 'benchmark_id' IS DISTINCT FROM NEW.benchmark_id
       OR NEW.payload_json::jsonb ->> 'benchmark_fingerprint' IS DISTINCT FROM NEW.benchmark_fingerprint
       OR NEW.payload_json::jsonb ->> 'rubric_version' IS DISTINCT FROM NEW.rubric_version
       OR NEW.payload_json::jsonb ->> 'reviewer_id' IS DISTINCT FROM NEW.reviewer_id
       OR NEW.payload_json::jsonb ->> 'reviewer_role' IS DISTINCT FROM NEW.reviewer_role
       OR NEW.payload_json::jsonb ->> 'decision' IS DISTINCT FROM NEW.decision THEN
        RAISE EXCEPTION 'acceptance review payload does not match indexed evidence'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_acceptance_reviews_validate_insert
BEFORE INSERT ON benchmark_acceptance_reviews
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_acceptance_review_insert();

CREATE FUNCTION reject_benchmark_acceptance_review_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark acceptance decisions are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_acceptance_reviews_no_update
BEFORE UPDATE OR DELETE ON benchmark_acceptance_reviews
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_acceptance_review_mutation();
