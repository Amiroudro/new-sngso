-- PostgreSQL migration 0001: transactional audit-chain storage foundation.
-- Apply inside one explicit transaction. This is NOT the full registry baseline.
-- The runtime role must not own these objects; role grants are provisioned separately.

CREATE TABLE IF NOT EXISTS postgres_schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    name text NOT NULL UNIQUE,
    checksum text NOT NULL CHECK (checksum ~ '^sha256:[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_events (
    sequence bigint PRIMARY KEY CHECK (sequence > 0),
    event_id text NOT NULL UNIQUE CHECK (length(btrim(event_id)) > 0),
    event_type text NOT NULL CHECK (length(btrim(event_type)) > 0),
    entity_type text NOT NULL CHECK (length(btrim(entity_type)) > 0),
    entity_id text NOT NULL CHECK (length(btrim(entity_id)) > 0),
    occurred_at text NOT NULL CHECK (length(btrim(occurred_at)) > 0),
    policy_version text,
    payload_json text NOT NULL
        CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    previous_hash text,
    event_hash text NOT NULL UNIQUE
        CHECK (event_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (previous_hash IS NULL OR previous_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK ((sequence = 1) = (previous_hash IS NULL))
);

CREATE TABLE audit_chain_head (
    singleton_id smallint PRIMARY KEY CHECK (singleton_id = 1),
    sequence bigint NOT NULL CHECK (sequence >= 0),
    event_hash text,
    CHECK (
        (sequence = 0 AND event_hash IS NULL)
        OR
        (sequence > 0 AND event_hash ~ '^sha256:[0-9a-f]{64}$')
    )
);

INSERT INTO audit_chain_head (singleton_id, sequence, event_hash)
VALUES (1, 0, NULL);

CREATE INDEX audit_events_entity_sequence
    ON audit_events (entity_type, entity_id, sequence);

CREATE FUNCTION reject_audit_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append-only'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation();

CREATE TRIGGER audit_events_no_delete
BEFORE DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_chain_head FROM PUBLIC;
