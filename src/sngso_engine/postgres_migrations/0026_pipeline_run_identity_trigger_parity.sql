-- PostgreSQL migration 0026: align pipeline-run identity fields with SQLite.
--
-- SQLite migration 26 makes id, started_at, software_version, config_hash,
-- input_hashes, and initiated_by immutable. It does not make attempt or retry_of
-- immutable. Keep PostgreSQL's direct-SQL behavior aligned with that contract.
CREATE OR REPLACE FUNCTION validate_pipeline_run_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
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
       OR payload -> 'initiated_by' IS DISTINCT FROM OLD.payload_json::jsonb -> 'initiated_by' THEN
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
