-- PostgreSQL migration 0013: durable pipeline stage checkpoints.
-- Ports the stage checkpoint registry from SQLite migration 32.

CREATE TABLE pipeline_stage_checkpoints (
    pipeline_run_id text NOT NULL,
    stage_ordinal integer NOT NULL CHECK (stage_ordinal >= 0),
    stage_name text NOT NULL CHECK (length(stage_name) BETWEEN 1 AND 100),
    input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^sha256:'),
    idempotency_key text NOT NULL UNIQUE CHECK (idempotency_key ~ '^sha256:'),
    status text NOT NULL CHECK (status IN (
        'executing', 'succeeded', 'failed', 'indeterminate', 'blocked'
    )),
    provider_namespace text,
    provider_operation_ref text,
    output_fingerprint text,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    updated_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    metadata_json text NOT NULL CHECK (jsonb_typeof(metadata_json::jsonb) = 'object'),
    PRIMARY KEY (pipeline_run_id, stage_ordinal),
    FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
    CHECK (provider_operation_ref IS NULL OR
        (length(provider_operation_ref) BETWEEN 1 AND 256
         AND provider_operation_ref !~ '[^A-Za-z0-9_.:-]')),
    CHECK (provider_namespace IS NULL OR
        (length(provider_namespace) BETWEEN 1 AND 100
         AND provider_namespace !~ '[^a-z0-9_.-]')),
    CHECK (
        (status = 'succeeded' AND output_fingerprint ~ '^sha256:')
        OR (status <> 'succeeded' AND output_fingerprint IS NULL)
    )
);

CREATE TABLE pipeline_stage_checkpoint_revisions (
    pipeline_run_id text NOT NULL,
    stage_ordinal integer NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    status text NOT NULL CHECK (status IN (
        'executing', 'succeeded', 'failed', 'indeterminate', 'blocked'
    )),
    provider_operation_ref text,
    output_fingerprint text,
    metadata_json text NOT NULL CHECK (jsonb_typeof(metadata_json::jsonb) = 'object'),
    PRIMARY KEY (pipeline_run_id, stage_ordinal, revision),
    FOREIGN KEY (pipeline_run_id, stage_ordinal)
        REFERENCES pipeline_stage_checkpoints(pipeline_run_id, stage_ordinal) ON DELETE RESTRICT
);
CREATE INDEX pipeline_stage_checkpoints_status
    ON pipeline_stage_checkpoints(status, updated_at);

CREATE FUNCTION validate_pipeline_checkpoint_metadata()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    item record;
BEGIN
    FOR item IN SELECT key, value FROM jsonb_each(NEW.metadata_json::jsonb)
    LOOP
        IF item.key NOT IN (
            'adapter_version', 'provider_state', 'failure_code',
            'reconciliation', 'attempt'
        ) THEN
            RAISE EXCEPTION 'stage checkpoint metadata contains unsupported fields or types'
                USING ERRCODE = '23514';
        END IF;
        IF item.key = 'attempt' THEN
            IF jsonb_typeof(item.value) IS DISTINCT FROM 'number'
               OR (item.value #>> '{}') !~ '^[0-9]+$'
               OR (item.value #>> '{}')::integer NOT BETWEEN 1 AND 1000 THEN
                RAISE EXCEPTION 'stage checkpoint metadata contains unsupported fields or types'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            IF jsonb_typeof(item.value) IS DISTINCT FROM 'string'
               OR length(item.value #>> '{}') NOT BETWEEN 1 AND 128
               OR (item.value #>> '{}') ~ '[^A-Za-z0-9_.:-]' THEN
                RAISE EXCEPTION 'stage checkpoint metadata contains unsupported fields or types'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_stage_checkpoint_metadata_guard_insert
BEFORE INSERT ON pipeline_stage_checkpoints
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_checkpoint_metadata();
CREATE TRIGGER pipeline_stage_checkpoint_metadata_guard_update
BEFORE UPDATE ON pipeline_stage_checkpoints
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_checkpoint_metadata();

CREATE FUNCTION validate_pipeline_stage_checkpoint_write()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> 1 THEN
            RAISE EXCEPTION 'initial stage checkpoint revision must be one'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'stage checkpoint revisions must increment sequentially'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id
       OR NEW.stage_ordinal IS DISTINCT FROM OLD.stage_ordinal
       OR NEW.stage_name IS DISTINCT FROM OLD.stage_name
       OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.provider_namespace IS DISTINCT FROM OLD.provider_namespace THEN
        RAISE EXCEPTION 'stage checkpoint identity is immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.provider_operation_ref IS NOT NULL
       AND NEW.provider_operation_ref IS DISTINCT FROM OLD.provider_operation_ref THEN
        RAISE EXCEPTION 'provider operation reference is immutable once recorded'
            USING ERRCODE = '23514';
    END IF;
    IF NOT (
        (OLD.status = 'executing' AND NEW.status IN ('succeeded', 'failed', 'indeterminate', 'blocked'))
        OR (OLD.status = 'indeterminate' AND NEW.status IN ('executing', 'succeeded', 'blocked'))
        OR (OLD.status = 'failed' AND NEW.status = 'blocked')
    ) THEN
        RAISE EXCEPTION 'invalid stage checkpoint status transition'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'succeeded' AND NEW.output_fingerprint IS NULL THEN
        RAISE EXCEPTION 'successful checkpoint requires an output fingerprint'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status <> 'succeeded' AND NEW.output_fingerprint IS NOT NULL THEN
        RAISE EXCEPTION 'non-successful checkpoint cannot carry an output fingerprint'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_stage_checkpoints_validate_write
BEFORE INSERT OR UPDATE ON pipeline_stage_checkpoints
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_stage_checkpoint_write();

CREATE FUNCTION record_pipeline_stage_checkpoint_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO pipeline_stage_checkpoint_revisions (
        pipeline_run_id, stage_ordinal, revision, created_at, actor, reason,
        status, provider_operation_ref, output_fingerprint, metadata_json
    ) VALUES (
        NEW.pipeline_run_id, NEW.stage_ordinal, NEW.revision, NEW.updated_at,
        NEW.actor, NEW.reason, NEW.status, NEW.provider_operation_ref,
        NEW.output_fingerprint, NEW.metadata_json
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_stage_checkpoint_initial_revision
AFTER INSERT ON pipeline_stage_checkpoints
FOR EACH ROW EXECUTE FUNCTION record_pipeline_stage_checkpoint_revision();
CREATE TRIGGER pipeline_stage_checkpoint_revision_snapshot
AFTER UPDATE ON pipeline_stage_checkpoints
FOR EACH ROW EXECUTE FUNCTION record_pipeline_stage_checkpoint_revision();

CREATE FUNCTION validate_pipeline_stage_checkpoint_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pipeline_stage_checkpoints checkpoint
         WHERE checkpoint.pipeline_run_id = NEW.pipeline_run_id
           AND checkpoint.stage_ordinal = NEW.stage_ordinal
           AND checkpoint.revision = NEW.revision
           AND checkpoint.updated_at = NEW.created_at
           AND checkpoint.actor = NEW.actor
           AND checkpoint.reason = NEW.reason
           AND checkpoint.status = NEW.status
           AND checkpoint.provider_operation_ref IS NOT DISTINCT FROM NEW.provider_operation_ref
           AND checkpoint.output_fingerprint IS NOT DISTINCT FROM NEW.output_fingerprint
           AND checkpoint.metadata_json = NEW.metadata_json
    ) THEN
        RAISE EXCEPTION 'checkpoint revision must match its current head'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_stage_checkpoint_revisions_validate_insert
BEFORE INSERT ON pipeline_stage_checkpoint_revisions
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_stage_checkpoint_revision_insert();

CREATE FUNCTION reject_pipeline_stage_checkpoint_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'stage checkpoint revisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER pipeline_stage_checkpoint_revisions_no_update
BEFORE UPDATE OR DELETE ON pipeline_stage_checkpoint_revisions
FOR EACH ROW EXECUTE FUNCTION reject_pipeline_stage_checkpoint_revision_mutation();

CREATE FUNCTION reject_pipeline_stage_checkpoint_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'stage checkpoints cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER pipeline_stage_checkpoints_no_delete
BEFORE DELETE ON pipeline_stage_checkpoints
FOR EACH ROW EXECUTE FUNCTION reject_pipeline_stage_checkpoint_delete();
