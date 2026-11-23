-- PostgreSQL migration 0019: append-only staging events and channel state.
-- Ports SQLite migration 31. This schema/guard layer is not itself the
-- application transaction that appends the global audit event.

CREATE TABLE edition_staging_events (
    event_id text PRIMARY KEY CHECK (length(btrim(event_id)) > 0),
    state_revision integer NOT NULL UNIQUE CHECK (state_revision >= 1),
    channel text NOT NULL CHECK (channel = 'staging'),
    operation text NOT NULL CHECK (operation IN ('promote', 'rollback')),
    manifest_id text NOT NULL
        REFERENCES edition_artifacts(manifest_id) ON DELETE RESTRICT,
    edition_id text NOT NULL,
    edition_revision integer NOT NULL CHECK (edition_revision >= 1),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^sha256:'),
    export_id text NOT NULL,
    export_sha256 text NOT NULL CHECK (export_sha256 ~ '^sha256:'),
    previous_current_export_id text,
    lkg_export_id text,
    operator_principal_id text NOT NULL
        REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    occurred_at text NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    export_package_json text NOT NULL CHECK (jsonb_typeof(export_package_json::jsonb) = 'object'),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object')
);

CREATE TABLE edition_staging_channel_state (
    channel text PRIMARY KEY CHECK (channel = 'staging'),
    state_revision integer NOT NULL CHECK (state_revision >= 0),
    current_event_id text
        REFERENCES edition_staging_events(event_id) ON DELETE RESTRICT,
    current_export_id text,
    lkg_event_id text
        REFERENCES edition_staging_events(event_id) ON DELETE RESTRICT,
    lkg_export_id text,
    CHECK ((current_event_id IS NULL) = (current_export_id IS NULL)),
    CHECK ((lkg_event_id IS NULL) = (lkg_export_id IS NULL))
);

INSERT INTO edition_staging_channel_state
    (channel, state_revision, current_event_id, current_export_id, lkg_event_id, lkg_export_id)
VALUES ('staging', 0, NULL, NULL, NULL, NULL);

CREATE FUNCTION validate_edition_staging_event_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    export_package jsonb;
    current_state edition_staging_channel_state%ROWTYPE;
BEGIN
    payload := NEW.payload_json::jsonb;
    export_package := NEW.export_package_json::jsonb;
    SELECT * INTO current_state
      FROM edition_staging_channel_state
     WHERE channel = 'staging'
     FOR UPDATE;

    IF payload ->> 'event_id' IS DISTINCT FROM NEW.event_id
       OR (payload ->> 'state_revision') IS DISTINCT FROM NEW.state_revision::text
       OR payload ->> 'channel' IS DISTINCT FROM 'staging'
       OR payload ->> 'operation' IS DISTINCT FROM NEW.operation
       OR payload ->> 'manifest_id' IS DISTINCT FROM NEW.manifest_id
       OR payload ->> 'edition_id' IS DISTINCT FROM NEW.edition_id
       OR (payload ->> 'edition_revision') IS DISTINCT FROM NEW.edition_revision::text
       OR payload ->> 'payload_sha256' IS DISTINCT FROM NEW.payload_sha256
       OR payload ->> 'export_id' IS DISTINCT FROM NEW.export_id
       OR payload ->> 'export_sha256' IS DISTINCT FROM NEW.export_sha256
       OR payload ->> 'previous_current_export_id' IS DISTINCT FROM NEW.previous_current_export_id
       OR payload ->> 'lkg_export_id' IS DISTINCT FROM NEW.lkg_export_id
       OR payload ->> 'operator_principal_id' IS DISTINCT FROM NEW.operator_principal_id
       OR payload ->> 'occurred_at' IS DISTINCT FROM NEW.occurred_at
       OR payload ->> 'reason' IS DISTINCT FROM NEW.reason
       OR payload -> 'publication_authorized' IS DISTINCT FROM 'false'::jsonb
       OR payload ->> 'target' IS DISTINCT FROM 'staging'
       OR NOT EXISTS (
           SELECT 1
             FROM edition_artifacts artifact
             JOIN edition_decisions decision
               ON decision.manifest_id = artifact.manifest_id
             JOIN edition_release_authorizations release_auth
               ON release_auth.manifest_id = artifact.manifest_id
            WHERE artifact.manifest_id = NEW.manifest_id
              AND artifact.edition_id = NEW.edition_id
              AND artifact.edition_revision = NEW.edition_revision
              AND artifact.payload_sha256 = NEW.payload_sha256
              AND decision.decision = 'approve'
              AND decision.decision_id = payload #>> '{staging_decision,decision_id}'
              AND decision.principal_id = payload #>> '{staging_decision,principal_id}'
              AND release_auth.authorization_id = payload #>> '{release_authorization,authorization_id}'
              AND release_auth.authorizer_principal_id = payload #>> '{release_authorization,authorizer_principal_id}'
              AND release_auth.approval_decision_id = decision.decision_id
              AND release_auth.approver_principal_id = decision.principal_id
              AND release_auth.authorizer_principal_id <> decision.principal_id
       )
       OR export_package ->> 'export_id' IS DISTINCT FROM NEW.export_id
       OR export_package ->> 'manifest_id' IS DISTINCT FROM NEW.manifest_id
       OR export_package ->> 'payload_sha256' IS DISTINCT FROM NEW.payload_sha256
       OR export_package ->> 'target' IS DISTINCT FROM 'staging'
       OR export_package -> 'publication_authorized' IS DISTINCT FROM 'false'::jsonb
       OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals approver
            WHERE approver.principal_id = payload #>> '{staging_decision,principal_id}'
              AND approver.active = 1 AND approver.principal_kind = 'human'
              AND approver.roles_json::jsonb @> '["edition_approver"]'::jsonb
       )
       OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals authorizer
            WHERE authorizer.principal_id = payload #>> '{release_authorization,authorizer_principal_id}'
              AND authorizer.active = 1 AND authorizer.principal_kind = 'human'
              AND authorizer.roles_json::jsonb @> '["edition_release_authorizer"]'::jsonb
       )
       OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals operator
            WHERE operator.principal_id = NEW.operator_principal_id
              AND operator.active = 1 AND operator.principal_kind = 'human'
              AND operator.roles_json::jsonb @> '["edition_staging_operator"]'::jsonb
       )
       OR NEW.operator_principal_id = payload #>> '{staging_decision,principal_id}'
       OR NEW.operator_principal_id = payload #>> '{release_authorization,authorizer_principal_id}'
       OR NEW.state_revision <> current_state.state_revision + 1
       OR NEW.previous_current_export_id IS DISTINCT FROM current_state.current_export_id
       OR NEW.lkg_export_id IS DISTINCT FROM NEW.previous_current_export_id
       OR (
           NEW.operation = 'promote'
           AND EXISTS (
               SELECT 1 FROM edition_staging_events prior
                WHERE prior.export_id = NEW.export_id AND prior.operation = 'promote'
           )
       )
       OR (
           NEW.operation = 'rollback'
           AND NOT EXISTS (
               SELECT 1 FROM edition_staging_events prior
                WHERE prior.export_id = NEW.export_id AND prior.operation = 'promote'
           )
       ) THEN
        RAISE EXCEPTION 'staging event failed immutable artifact, authority, operator, or state guard'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_staging_events_insert_guard
BEFORE INSERT ON edition_staging_events
FOR EACH ROW EXECUTE FUNCTION validate_edition_staging_event_insert();

CREATE FUNCTION reject_edition_staging_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'staging event history is append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER edition_staging_events_no_update
BEFORE UPDATE OR DELETE ON edition_staging_events
FOR EACH ROW EXECUTE FUNCTION reject_edition_staging_event_mutation();

CREATE FUNCTION validate_edition_staging_state_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.state_revision <> OLD.state_revision + 1
       OR NOT EXISTS (
           SELECT 1 FROM edition_staging_events event
            WHERE event.event_id = NEW.current_event_id
              AND event.state_revision = NEW.state_revision
              AND event.export_id = NEW.current_export_id
              AND event.lkg_export_id IS NOT DISTINCT FROM NEW.lkg_export_id
              AND event.previous_current_export_id IS NOT DISTINCT FROM OLD.current_export_id
              AND NEW.lkg_event_id IS NOT DISTINCT FROM OLD.current_event_id
       ) THEN
        RAISE EXCEPTION 'staging state must advance atomically from a matching append-only event'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_staging_state_update_guard
BEFORE UPDATE ON edition_staging_channel_state
FOR EACH ROW EXECUTE FUNCTION validate_edition_staging_state_update();

CREATE FUNCTION reject_edition_staging_state_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'staging channel state cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER edition_staging_state_no_delete
BEFORE DELETE ON edition_staging_channel_state
FOR EACH ROW EXECUTE FUNCTION reject_edition_staging_state_delete();
