-- PostgreSQL migration 0025: prevent orphan future benchmark assignment revisions.
--
-- The insert validator intentionally permits a snapshot for the next legal
-- transition so it can be written before the assignment head in one transaction.
-- Defer the head/snapshot equivalence check until commit.
CREATE FUNCTION ensure_benchmark_assignment_revision_not_ahead_of_head()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_revision integer;
    current_payload jsonb;
BEGIN
    SELECT revision, payload_json::jsonb
      INTO current_revision, current_payload
      FROM benchmark_review_assignments
     WHERE assignment_id = NEW.assignment_id;

    IF NOT FOUND OR current_revision < NEW.revision THEN
        RAISE EXCEPTION 'benchmark assignment revision cannot be committed ahead of its current head'
            USING ERRCODE = '23514';
    END IF;

    IF current_revision = NEW.revision
       AND current_payload IS DISTINCT FROM NEW.payload_json::jsonb THEN
        RAISE EXCEPTION 'benchmark assignment current head must match its revision snapshot'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER benchmark_assignment_revision_head_consistency
AFTER INSERT ON benchmark_review_assignment_revisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION ensure_benchmark_assignment_revision_not_ahead_of_head();
