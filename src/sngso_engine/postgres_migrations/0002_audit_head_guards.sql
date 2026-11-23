-- PostgreSQL migration 0002: make audit-chain head movement append-only.
-- A runtime writer may INSERT the next event and advance the singleton head,
-- but cannot skip a sequence, fork the predecessor, or rewrite the head.
-- Event hashes remain application-canonical; this guard enforces chain shape,
-- not the application's canonical JSON hashing algorithm.

CREATE FUNCTION guard_audit_event_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    head_sequence bigint;
    head_hash text;
BEGIN
    SELECT sequence, event_hash
      INTO head_sequence, head_hash
      FROM audit_chain_head
     WHERE singleton_id = 1
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit chain head is missing'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.sequence <> head_sequence + 1
       OR NEW.previous_hash IS DISTINCT FROM head_hash THEN
        RAISE EXCEPTION 'audit event must extend the current chain head'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER audit_events_insert_guard
BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION guard_audit_event_insert();

CREATE FUNCTION guard_audit_chain_head_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    inserted_previous_hash text;
    inserted_event_hash text;
BEGIN
    IF NEW.singleton_id <> OLD.singleton_id
       OR NEW.sequence <> OLD.sequence + 1 THEN
        RAISE EXCEPTION 'audit chain head may advance exactly one sequence'
            USING ERRCODE = '23514';
    END IF;

    SELECT previous_hash, event_hash
      INTO inserted_previous_hash, inserted_event_hash
      FROM audit_events
     WHERE sequence = NEW.sequence;

    IF NOT FOUND
       OR inserted_previous_hash IS DISTINCT FROM OLD.event_hash
       OR inserted_event_hash IS DISTINCT FROM NEW.event_hash THEN
        RAISE EXCEPTION 'audit chain head must match the appended successor event'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER audit_chain_head_update_guard
BEFORE UPDATE ON audit_chain_head
FOR EACH ROW EXECUTE FUNCTION guard_audit_chain_head_update();

CREATE FUNCTION reject_audit_chain_head_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit chain head cannot be deleted'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER audit_chain_head_no_delete
BEFORE DELETE ON audit_chain_head
FOR EACH ROW EXECUTE FUNCTION reject_audit_chain_head_delete();
