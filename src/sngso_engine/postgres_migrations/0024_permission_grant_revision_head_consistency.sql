-- PostgreSQL migration 0024: prevent orphan future permission-grant revisions.
--
-- Revision inserts may precede the matching head update inside one transaction.
-- Defer the consistency check until commit so valid atomic transitions remain
-- possible while a standalone future snapshot cannot be committed.
CREATE FUNCTION ensure_permission_grant_revision_not_ahead_of_head()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_revision integer;
    current_payload jsonb;
BEGIN
    SELECT revision, payload_json::jsonb
      INTO current_revision, current_payload
      FROM permission_grants
     WHERE id = NEW.grant_id;

    IF NOT FOUND OR current_revision < NEW.revision THEN
        RAISE EXCEPTION 'permission grant revision cannot be committed ahead of its current head'
            USING ERRCODE = '23514';
    END IF;

    IF current_revision = NEW.revision
       AND current_payload IS DISTINCT FROM NEW.payload_json::jsonb THEN
        RAISE EXCEPTION 'permission grant current head must match its revision snapshot'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER permission_grant_revision_head_consistency
AFTER INSERT ON permission_grant_revisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION ensure_permission_grant_revision_not_ahead_of_head();
