-- PostgreSQL migration 0008: benchmark artifacts, principals, reviews and adjudications.
-- Ports SQLite migrations 13-16 and the final principal columns from migrations 17-18.

CREATE TABLE benchmark_artifacts (
    artifact_id text PRIMARY KEY CHECK (length(btrim(artifact_id)) > 0),
    language text NOT NULL CHECK (language IN ('bn', 'en')),
    canonicalization text NOT NULL CHECK (canonicalization = 'canonical-json-v1'),
    content_fingerprint text NOT NULL CHECK (content_fingerprint LIKE 'sha256:%'),
    content_json text NOT NULL CHECK (content_json::jsonb IS NOT NULL),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0)
);
CREATE FUNCTION reject_benchmark_artifact_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark artifacts are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_artifacts_no_update
BEFORE UPDATE OR DELETE ON benchmark_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_artifact_mutation();

CREATE TABLE benchmark_principals (
    principal_id text PRIMARY KEY CHECK (length(btrim(principal_id)) > 0),
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    roles_json text NOT NULL CHECK (roles_json::jsonb IS NOT NULL),
    active integer NOT NULL CHECK (active IN (0, 1)),
    created_at text NOT NULL,
    created_by text NOT NULL CHECK (length(btrim(created_by)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    languages_json text NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(languages_json::jsonb) = 'array'),
    principal_kind text NOT NULL DEFAULT 'unverified'
        CHECK (principal_kind IN ('human', 'synthetic', 'automation', 'unverified'))
);
CREATE FUNCTION reject_benchmark_principal_kind_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.principal_kind IS DISTINCT FROM OLD.principal_kind THEN
        RAISE EXCEPTION 'benchmark principal classification is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_principal_kind_immutable
BEFORE UPDATE OF principal_kind ON benchmark_principals
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_principal_kind_change();

CREATE TABLE benchmark_reviews (
    review_id text PRIMARY KEY CHECK (length(btrim(review_id)) > 0),
    benchmark_id text NOT NULL,
    benchmark_fingerprint text NOT NULL CHECK (benchmark_fingerprint LIKE 'sha256:%'),
    language text NOT NULL CHECK (language IN ('bn', 'en')),
    status text NOT NULL CHECK (status IN ('draft', 'approved', 'rejected')),
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    updated_at text NOT NULL,
    last_actor text NOT NULL CHECK (length(btrim(last_actor)) > 0),
    last_reason text NOT NULL CHECK (length(btrim(last_reason)) > 0),
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL)
);
CREATE INDEX idx_benchmark_reviews_benchmark
    ON benchmark_reviews(benchmark_id, language, status);

CREATE TABLE benchmark_review_revisions (
    review_id text NOT NULL REFERENCES benchmark_reviews(review_id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    PRIMARY KEY (review_id, revision)
);

CREATE FUNCTION validate_benchmark_review_write()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> 1 THEN
            RAISE EXCEPTION 'benchmark review must start at revision one'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.review_id IS DISTINCT FROM OLD.review_id
           OR NEW.benchmark_id IS DISTINCT FROM OLD.benchmark_id
           OR NEW.benchmark_fingerprint IS DISTINCT FROM OLD.benchmark_fingerprint
           OR NEW.language IS DISTINCT FROM OLD.language
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'benchmark review identity and fingerprint binding are immutable'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'benchmark review revision must increment by one'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.updated_at::timestamptz < OLD.updated_at::timestamptz THEN
            RAISE EXCEPTION 'benchmark review update time cannot move backwards'
                USING ERRCODE = '23514';
        END IF;
        IF btrim(NEW.last_actor) = '' OR btrim(NEW.last_reason) = '' THEN
            RAISE EXCEPTION 'benchmark review revision requires actor and reason'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_reviews_validate_write
BEFORE INSERT OR UPDATE ON benchmark_reviews
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_review_write();

CREATE FUNCTION reject_benchmark_review_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark reviews cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_reviews_no_delete
BEFORE DELETE ON benchmark_reviews
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_review_delete();

CREATE FUNCTION validate_benchmark_review_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM benchmark_reviews
         WHERE review_id = NEW.review_id
           AND revision = NEW.revision
           AND payload_json = NEW.payload_json
    ) THEN
        RAISE EXCEPTION 'benchmark review revision must match the current review'
            USING ERRCODE = '23514';
    END IF;
    IF btrim(NEW.actor) = '' OR btrim(NEW.reason) = '' THEN
        RAISE EXCEPTION 'benchmark review revision requires actor and reason'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_review_revisions_validate_insert
BEFORE INSERT ON benchmark_review_revisions
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_review_revision_insert();

CREATE FUNCTION record_benchmark_review_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO benchmark_review_revisions
        (review_id, revision, created_at, actor, reason, payload_json)
    VALUES (
        NEW.review_id, NEW.revision,
        CASE WHEN TG_OP = 'INSERT' THEN NEW.created_at ELSE NEW.updated_at END,
        NEW.last_actor, NEW.last_reason, NEW.payload_json
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_reviews_record_revision
AFTER INSERT OR UPDATE ON benchmark_reviews
FOR EACH ROW EXECUTE FUNCTION record_benchmark_review_revision();

CREATE FUNCTION reject_benchmark_review_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark review revisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_review_revisions_no_update
BEFORE UPDATE OR DELETE ON benchmark_review_revisions
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_review_revision_mutation();

CREATE TABLE benchmark_adjudications (
    adjudication_id text PRIMARY KEY CHECK (length(btrim(adjudication_id)) > 0),
    benchmark_id text NOT NULL,
    benchmark_fingerprint text NOT NULL,
    adjudicator_id text NOT NULL,
    resolved_at text NOT NULL,
    payload_json text NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    created_at text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL
);
CREATE FUNCTION validate_benchmark_adjudication_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM benchmark_adjudications WHERE benchmark_id = NEW.benchmark_id) THEN
        RAISE EXCEPTION 'benchmark artifact already has a final adjudication'
            USING ERRCODE = '23505';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_adjudications_one_per_artifact
BEFORE INSERT ON benchmark_adjudications
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_adjudication_insert();

CREATE FUNCTION reject_benchmark_adjudication_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'benchmark adjudications are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER benchmark_adjudications_no_update
BEFORE UPDATE OR DELETE ON benchmark_adjudications
FOR EACH ROW EXECUTE FUNCTION reject_benchmark_adjudication_mutation();
