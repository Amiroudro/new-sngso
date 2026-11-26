-- PostgreSQL migration 0018: independent human release authorization.
-- Ports SQLite migration 30. The authorization is distinct from the approval
-- decision and binds both authorities to the exact immutable artifact.

CREATE TABLE edition_release_authorizations (
    authorization_id text PRIMARY KEY CHECK (length(btrim(authorization_id)) > 0),
    manifest_id text NOT NULL UNIQUE
        REFERENCES edition_artifacts(manifest_id) ON DELETE RESTRICT,
    edition_id text NOT NULL,
    edition_revision integer NOT NULL CHECK (edition_revision >= 1),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^sha256:'),
    approval_decision_id text NOT NULL
        REFERENCES edition_decisions(decision_id) ON DELETE RESTRICT,
    approver_principal_id text NOT NULL
        REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    authorizer_principal_id text NOT NULL
        REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
    authorized_at text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    CHECK (approver_principal_id <> authorizer_principal_id)
);

CREATE FUNCTION validate_edition_release_authorization_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF payload ->> 'authorization_id' IS DISTINCT FROM NEW.authorization_id
       OR payload ->> 'manifest_id' IS DISTINCT FROM NEW.manifest_id
       OR payload ->> 'edition_id' IS DISTINCT FROM NEW.edition_id
       OR (payload ->> 'edition_revision') IS DISTINCT FROM NEW.edition_revision::text
       OR payload ->> 'payload_sha256' IS DISTINCT FROM NEW.payload_sha256
       OR payload ->> 'approval_decision_id' IS DISTINCT FROM NEW.approval_decision_id
       OR payload ->> 'approver_principal_id' IS DISTINCT FROM NEW.approver_principal_id
       OR payload ->> 'authorizer_principal_id' IS DISTINCT FROM NEW.authorizer_principal_id
       OR payload ->> 'authorized_at' IS DISTINCT FROM NEW.authorized_at
       OR payload ->> 'scope' IS DISTINCT FROM 'controlled_release_gate'
       OR payload -> 'release_authorized' IS DISTINCT FROM 'true'::jsonb
       OR payload -> 'publication_authorized' IS DISTINCT FROM 'false'::jsonb
       OR NOT EXISTS (
           SELECT 1 FROM edition_artifacts artifact
            WHERE artifact.manifest_id = NEW.manifest_id
              AND artifact.edition_id = NEW.edition_id
              AND artifact.edition_revision = NEW.edition_revision
              AND artifact.payload_sha256 = NEW.payload_sha256
       )
       OR NOT EXISTS (
           SELECT 1 FROM edition_decisions decision
            WHERE decision.decision_id = NEW.approval_decision_id
              AND decision.manifest_id = NEW.manifest_id
              AND decision.edition_id = NEW.edition_id
              AND decision.edition_revision = NEW.edition_revision
              AND decision.payload_sha256 = NEW.payload_sha256
              AND decision.decision = 'approve'
              AND decision.principal_id = NEW.approver_principal_id
       )
       OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals principal
            WHERE principal.principal_id = NEW.authorizer_principal_id
              AND principal.active = 1
              AND principal.principal_kind = 'human'
              AND principal.roles_json::jsonb @> '["edition_release_authorizer"]'::jsonb
       ) THEN
        RAISE EXCEPTION 'release authorization must bind an approved artifact and independent active human authorizer'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_release_authorizations_insert_guard
BEFORE INSERT ON edition_release_authorizations
FOR EACH ROW EXECUTE FUNCTION validate_edition_release_authorization_insert();

CREATE FUNCTION reject_edition_release_authorization_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'edition release authorizations are append-only'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER edition_release_authorizations_no_update
BEFORE UPDATE OR DELETE ON edition_release_authorizations
FOR EACH ROW EXECUTE FUNCTION reject_edition_release_authorization_mutation();
