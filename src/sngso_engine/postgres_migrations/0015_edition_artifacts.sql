-- PostgreSQL migration 0015: immutable edition artifact and manifest registry.
-- Ports SQLite migration 27. Artifact verification itself remains an application
-- responsibility; this table binds the verifier's receipt to exact manifest data.

CREATE TABLE edition_artifacts (
    manifest_id text PRIMARY KEY CHECK (length(btrim(manifest_id)) > 0),
    edition_id text NOT NULL CHECK (length(btrim(edition_id)) > 0),
    edition_revision integer NOT NULL CHECK (edition_revision >= 1),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^sha256:'),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    manifest_json text NOT NULL CHECK (jsonb_typeof(manifest_json::jsonb) = 'object'),
    verification_json text NOT NULL CHECK (jsonb_typeof(verification_json::jsonb) = 'object'),
    created_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    UNIQUE (edition_id, edition_revision)
);

CREATE FUNCTION validate_edition_artifact_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    manifest jsonb;
BEGIN
    manifest := NEW.manifest_json::jsonb;
    IF manifest ->> 'manifest_id' IS DISTINCT FROM NEW.manifest_id
       OR manifest ->> 'edition_id' IS DISTINCT FROM NEW.edition_id
       OR (manifest ->> 'edition_revision') IS DISTINCT FROM NEW.edition_revision::text
       OR manifest ->> 'payload_sha256' IS DISTINCT FROM NEW.payload_sha256 THEN
        RAISE EXCEPTION 'stored edition artifact columns must match manifest'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_artifacts_payload_guard
BEFORE INSERT ON edition_artifacts
FOR EACH ROW EXECUTE FUNCTION validate_edition_artifact_insert();

CREATE FUNCTION reject_edition_artifact_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'edition artifacts are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER edition_artifacts_no_update
BEFORE UPDATE OR DELETE ON edition_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_edition_artifact_mutation();
