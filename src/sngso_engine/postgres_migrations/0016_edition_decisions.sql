-- PostgreSQL migration 0016: immutable edition approval decisions.
-- Ports SQLite migration 28 and binds each decision to an exact artifact and
-- an active human principal with the edition_approver role.

CREATE TABLE edition_decisions (
    decision_id text PRIMARY KEY CHECK (length(btrim(decision_id)) > 0),
    manifest_id text NOT NULL UNIQUE
        REFERENCES edition_artifacts(manifest_id) ON DELETE RESTRICT,
    edition_id text NOT NULL,
    edition_revision integer NOT NULL CHECK (edition_revision >= 1),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^sha256:'),
    decision text NOT NULL CHECK (decision IN ('approve', 'reject')),
    principal_id text NOT NULL
        REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    decided_at text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object')
);

CREATE FUNCTION validate_edition_decision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF payload ->> 'decision_id' IS DISTINCT FROM NEW.decision_id
       OR payload ->> 'manifest_id' IS DISTINCT FROM NEW.manifest_id
       OR payload ->> 'edition_id' IS DISTINCT FROM NEW.edition_id
       OR (payload ->> 'edition_revision') IS DISTINCT FROM NEW.edition_revision::text
       OR payload ->> 'payload_sha256' IS DISTINCT FROM NEW.payload_sha256
       OR payload ->> 'decision' IS DISTINCT FROM NEW.decision
       OR payload ->> 'principal_id' IS DISTINCT FROM NEW.principal_id
       OR payload ->> 'decided_at' IS DISTINCT FROM NEW.decided_at
       OR payload ->> 'role' IS DISTINCT FROM 'edition_approver'
       OR payload ->> 'scope' IS DISTINCT FROM 'staging_candidate_review'
       OR payload -> 'publication_authorized' IS DISTINCT FROM 'false'::jsonb
       OR NOT EXISTS (
           SELECT 1 FROM edition_artifacts artifact
            WHERE artifact.manifest_id = NEW.manifest_id
              AND artifact.edition_id = NEW.edition_id
              AND artifact.edition_revision = NEW.edition_revision
              AND artifact.payload_sha256 = NEW.payload_sha256
       )
       OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals principal
            WHERE principal.principal_id = NEW.principal_id
              AND principal.active = 1
              AND principal.principal_kind = 'human'
              AND principal.roles_json::jsonb @> '["edition_approver"]'::jsonb
       ) THEN
        RAISE EXCEPTION 'edition decision must bind an exact artifact and active human approver'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_decisions_payload_guard
BEFORE INSERT ON edition_decisions
FOR EACH ROW EXECUTE FUNCTION validate_edition_decision_insert();

CREATE FUNCTION reject_edition_decision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'edition decisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER edition_decisions_no_update
BEFORE UPDATE OR DELETE ON edition_decisions
FOR EACH ROW EXECUTE FUNCTION reject_edition_decision_mutation();
