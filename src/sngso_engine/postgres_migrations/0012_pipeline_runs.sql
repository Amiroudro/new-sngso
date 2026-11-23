-- PostgreSQL migration 0012: durable pipeline run registry.
-- Ports the pipeline run and revision tables from SQLite migration 26.

CREATE TABLE pipeline_runs (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    status text NOT NULL CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed', 'cancelled', 'rejected'
    )),
    revision integer NOT NULL CHECK (revision >= 1),
    started_at text NOT NULL CHECK (length(btrim(started_at)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object')
);
CREATE INDEX pipeline_runs_status_started
    ON pipeline_runs(status, started_at);

CREATE TABLE pipeline_run_revisions (
    pipeline_run_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL CHECK (length(btrim(created_at)) > 0),
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    PRIMARY KEY (pipeline_run_id, revision),
    FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE RESTRICT
);

CREATE FUNCTION validate_pipeline_run_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF payload ->> 'id' IS DISTINCT FROM NEW.id
       OR payload ->> 'status' IS DISTINCT FROM NEW.status
       OR payload ->> 'started_at' IS DISTINCT FROM NEW.started_at THEN
        RAISE EXCEPTION 'pipeline run indexed fields must match payload'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('queued', 'running') OR NEW.revision <> 1 THEN
            RAISE EXCEPTION 'new pipeline run must start at revision one in queued or running state'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR payload -> 'software_version' IS DISTINCT FROM OLD.payload_json::jsonb -> 'software_version'
       OR payload -> 'config_hash' IS DISTINCT FROM OLD.payload_json::jsonb -> 'config_hash'
       OR payload -> 'input_hashes' IS DISTINCT FROM OLD.payload_json::jsonb -> 'input_hashes'
       OR payload -> 'initiated_by' IS DISTINCT FROM OLD.payload_json::jsonb -> 'initiated_by'
       OR payload -> 'attempt' IS DISTINCT FROM OLD.payload_json::jsonb -> 'attempt'
       OR payload -> 'retry_of' IS DISTINCT FROM OLD.payload_json::jsonb -> 'retry_of' THEN
        RAISE EXCEPTION 'pipeline run identity and inputs are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'pipeline run revision must increment by one'
            USING ERRCODE = '23514';
    END IF;
    IF NOT (
        (OLD.status = 'queued' AND NEW.status IN ('running', 'cancelled', 'rejected'))
        OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'cancelled'))
    ) THEN
        RAISE EXCEPTION 'invalid pipeline run status transition'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_runs_validate_write
BEFORE INSERT OR UPDATE ON pipeline_runs
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_run_write();

CREATE FUNCTION reject_pipeline_run_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'pipeline run records cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER pipeline_runs_no_delete
BEFORE DELETE ON pipeline_runs
FOR EACH ROW EXECUTE FUNCTION reject_pipeline_run_delete();

CREATE FUNCTION validate_pipeline_run_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pipeline_runs
         WHERE id = NEW.pipeline_run_id
           AND revision = NEW.revision
           AND payload_json = NEW.payload_json
    ) THEN
        RAISE EXCEPTION 'pipeline run revision must match current record'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_run_revisions_validate_insert
BEFORE INSERT ON pipeline_run_revisions
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_run_revision_insert();

CREATE FUNCTION reject_pipeline_run_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'pipeline run revisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER pipeline_run_revisions_no_update
BEFORE UPDATE OR DELETE ON pipeline_run_revisions
FOR EACH ROW EXECUTE FUNCTION reject_pipeline_run_revision_mutation();
