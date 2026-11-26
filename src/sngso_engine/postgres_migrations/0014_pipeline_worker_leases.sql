-- PostgreSQL migration 0014: fenced pipeline worker leases.
-- Ports the worker lease lifecycle and append-only revisions from SQLite migration 33.

CREATE TABLE pipeline_worker_leases (
    pipeline_run_id text PRIMARY KEY
        REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
    worker_id text NOT NULL CHECK (
        length(worker_id) BETWEEN 1 AND 100 AND worker_id !~ '[^A-Za-z0-9_.:-]'
    ),
    token_hash text NOT NULL CHECK (token_hash ~ '^sha256:'),
    fencing_token integer NOT NULL CHECK (fencing_token >= 1),
    status text NOT NULL CHECK (status IN ('active', 'released', 'expired', 'cancelled')),
    acquired_at text NOT NULL,
    heartbeat_at text NOT NULL,
    expires_at text NOT NULL,
    cancel_requested_at text,
    cancel_requested_by text,
    cancel_reason_code text,
    revision integer NOT NULL CHECK (revision >= 1),
    updated_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    CHECK (
        (cancel_requested_at IS NULL AND cancel_requested_by IS NULL AND cancel_reason_code IS NULL)
        OR (
            cancel_requested_at IS NOT NULL AND cancel_requested_by IS NOT NULL
            AND cancel_reason_code IS NOT NULL
            AND cancel_reason_code ~ '^[a-z][a-z0-9_-]{0,99}$'
        )
    )
);

CREATE TABLE pipeline_worker_lease_revisions (
    pipeline_run_id text NOT NULL
        REFERENCES pipeline_worker_leases(pipeline_run_id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    worker_id text NOT NULL,
    token_hash text NOT NULL,
    fencing_token integer NOT NULL,
    status text NOT NULL,
    acquired_at text NOT NULL,
    heartbeat_at text NOT NULL,
    expires_at text NOT NULL,
    cancel_requested_at text,
    cancel_requested_by text,
    cancel_reason_code text,
    actor text NOT NULL,
    reason text NOT NULL,
    PRIMARY KEY (pipeline_run_id, revision)
);
CREATE INDEX pipeline_worker_leases_expiry
    ON pipeline_worker_leases(status, expires_at);

CREATE FUNCTION validate_pipeline_worker_lease_write()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> 1 OR NEW.status <> 'active'
           OR NEW.expires_at::timestamptz <= NEW.heartbeat_at::timestamptz
           OR NEW.acquired_at IS DISTINCT FROM NEW.heartbeat_at THEN
            RAISE EXCEPTION 'initial worker lease must be active with a fresh fencing epoch'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'worker lease revisions must increment sequentially'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id THEN
        RAISE EXCEPTION 'worker lease run identity is immutable'
            USING ERRCODE = '23514';
    END IF;
    IF NOT (
        (
            OLD.status = 'active' AND NEW.status = 'active'
            AND NEW.worker_id = OLD.worker_id
            AND NEW.token_hash = OLD.token_hash
            AND NEW.fencing_token = OLD.fencing_token
            AND NEW.acquired_at = OLD.acquired_at
            AND NEW.heartbeat_at::timestamptz >= OLD.heartbeat_at::timestamptz
            AND NEW.expires_at::timestamptz > NEW.heartbeat_at::timestamptz
        )
        OR (
            OLD.status = 'active' AND NEW.status IN ('released', 'expired', 'cancelled')
            AND NEW.worker_id = OLD.worker_id
            AND NEW.token_hash = OLD.token_hash
            AND NEW.fencing_token = OLD.fencing_token
            AND NEW.acquired_at = OLD.acquired_at
        )
        OR (
            OLD.status IN ('released', 'expired') AND NEW.status = 'active'
            AND NEW.fencing_token = OLD.fencing_token + 1
            AND NEW.token_hash <> OLD.token_hash
            AND NEW.acquired_at = NEW.heartbeat_at
            AND NEW.expires_at::timestamptz > NEW.heartbeat_at::timestamptz
        )
    ) THEN
        RAISE EXCEPTION 'invalid worker lease ownership or status transition'
            USING ERRCODE = '23514';
    END IF;

    IF OLD.cancel_requested_at IS NOT NULL AND (
        NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at
        OR NEW.cancel_requested_by IS DISTINCT FROM OLD.cancel_requested_by
        OR NEW.cancel_reason_code IS DISTINCT FROM OLD.cancel_reason_code
    ) THEN
        RAISE EXCEPTION 'worker cancellation request is immutable once recorded'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.cancel_requested_at IS NULL AND NEW.cancel_requested_at IS NOT NULL
       AND (
           NEW.status <> 'active'
           OR NEW.cancel_requested_by IS NULL
           OR NEW.cancel_reason_code IS NULL
       ) THEN
        RAISE EXCEPTION 'cancellation request requires an active lease and complete request'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_worker_leases_validate_write
BEFORE INSERT OR UPDATE ON pipeline_worker_leases
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_worker_lease_write();

CREATE FUNCTION record_pipeline_worker_lease_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO pipeline_worker_lease_revisions (
        pipeline_run_id, revision, created_at, worker_id, token_hash, fencing_token,
        status, acquired_at, heartbeat_at, expires_at, cancel_requested_at,
        cancel_requested_by, cancel_reason_code, actor, reason
    ) VALUES (
        NEW.pipeline_run_id, NEW.revision, NEW.updated_at, NEW.worker_id,
        NEW.token_hash, NEW.fencing_token, NEW.status, NEW.acquired_at,
        NEW.heartbeat_at, NEW.expires_at, NEW.cancel_requested_at,
        NEW.cancel_requested_by, NEW.cancel_reason_code, NEW.actor, NEW.reason
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_worker_lease_initial_revision
AFTER INSERT ON pipeline_worker_leases
FOR EACH ROW EXECUTE FUNCTION record_pipeline_worker_lease_revision();
CREATE TRIGGER pipeline_worker_lease_revision_snapshot
AFTER UPDATE ON pipeline_worker_leases
FOR EACH ROW EXECUTE FUNCTION record_pipeline_worker_lease_revision();

CREATE FUNCTION validate_pipeline_worker_lease_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pipeline_worker_leases lease
         WHERE lease.pipeline_run_id = NEW.pipeline_run_id
           AND lease.revision = NEW.revision
           AND lease.updated_at = NEW.created_at
           AND lease.worker_id = NEW.worker_id
           AND lease.token_hash = NEW.token_hash
           AND lease.fencing_token = NEW.fencing_token
           AND lease.status = NEW.status
           AND lease.acquired_at = NEW.acquired_at
           AND lease.heartbeat_at = NEW.heartbeat_at
           AND lease.expires_at = NEW.expires_at
           AND lease.cancel_requested_at IS NOT DISTINCT FROM NEW.cancel_requested_at
           AND lease.cancel_requested_by IS NOT DISTINCT FROM NEW.cancel_requested_by
           AND lease.cancel_reason_code IS NOT DISTINCT FROM NEW.cancel_reason_code
           AND lease.actor = NEW.actor
           AND lease.reason = NEW.reason
    ) THEN
        RAISE EXCEPTION 'worker lease revision must match current head'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pipeline_worker_lease_revisions_validate_insert
BEFORE INSERT ON pipeline_worker_lease_revisions
FOR EACH ROW EXECUTE FUNCTION validate_pipeline_worker_lease_revision_insert();

CREATE FUNCTION reject_pipeline_worker_lease_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'worker lease revisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER pipeline_worker_lease_revisions_no_update
BEFORE UPDATE OR DELETE ON pipeline_worker_lease_revisions
FOR EACH ROW EXECUTE FUNCTION reject_pipeline_worker_lease_revision_mutation();

CREATE FUNCTION reject_pipeline_worker_lease_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'worker leases cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER pipeline_worker_leases_no_delete
BEFORE DELETE ON pipeline_worker_leases
FOR EACH ROW EXECUTE FUNCTION reject_pipeline_worker_lease_delete();
