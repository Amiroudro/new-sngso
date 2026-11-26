-- PostgreSQL migration 0021: immutable domain identity and automatic revision snapshots.
-- Ports the update guard and snapshot behavior from SQLite migration 35.
-- The actor/reason/timestamp columns were present from PostgreSQL migration 0003.

CREATE FUNCTION validate_domain_record_revision_update()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    new_payload jsonb;
    old_payload jsonb;
BEGIN
    new_payload := NEW.payload_json::jsonb;
    old_payload := OLD.payload_json::jsonb;

    IF NEW.record_type IS DISTINCT FROM OLD.record_type
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'domain record identity and creation time are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'domain record revision must increment by one'
            USING ERRCODE = '23514';
    END IF;
    IF btrim(NEW.last_actor) = '' OR btrim(NEW.last_reason) = ''
       OR btrim(NEW.last_revision_at) = '' THEN
        RAISE EXCEPTION 'domain record revision requires actor, reason and timestamp'
            USING ERRCODE = '23514';
    END IF;
    IF new_payload ->> 'id' IS DISTINCT FROM NEW.id THEN
        RAISE EXCEPTION 'domain record payload ID must match indexed identity'
            USING ERRCODE = '23514';
    END IF;
    IF new_payload ? 'revision'
       AND (new_payload ->> 'revision') IS DISTINCT FROM NEW.revision::text THEN
        RAISE EXCEPTION 'domain record payload revision must match indexed revision'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.last_revision_at::timestamptz < OLD.last_revision_at::timestamptz THEN
        RAISE EXCEPTION 'domain record revision timestamp cannot move backwards'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.record_type = 'article' AND (
        new_payload -> 'source_id' IS DISTINCT FROM old_payload -> 'source_id'
        OR new_payload -> 'canonical_uri' IS DISTINCT FROM old_payload -> 'canonical_uri'
    ) THEN
        RAISE EXCEPTION 'article source and canonical identity are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.record_type = 'evidence' AND (
        new_payload -> 'source_id' IS DISTINCT FROM old_payload -> 'source_id'
        OR new_payload -> 'claim_id' IS DISTINCT FROM old_payload -> 'claim_id'
        OR new_payload -> 'article_id' IS DISTINCT FROM old_payload -> 'article_id'
        OR new_payload -> 'observation_id' IS DISTINCT FROM old_payload -> 'observation_id'
        OR new_payload -> 'locator' IS DISTINCT FROM old_payload -> 'locator'
    ) THEN
        RAISE EXCEPTION 'evidence provenance identity is immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER domain_records_revision_guard
BEFORE UPDATE ON domain_records
FOR EACH ROW WHEN (OLD.record_type <> 'edition')
EXECUTE FUNCTION validate_domain_record_revision_update();

CREATE FUNCTION record_domain_record_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO domain_record_revisions (
        record_type, record_id, revision, created_at, actor, reason, payload_json
    ) VALUES (
        NEW.record_type, NEW.id, NEW.revision, NEW.last_revision_at,
        NEW.last_actor, NEW.last_reason, NEW.payload_json
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER domain_records_record_update
AFTER UPDATE ON domain_records
FOR EACH ROW WHEN (OLD.record_type <> 'edition')
EXECUTE FUNCTION record_domain_record_revision();
