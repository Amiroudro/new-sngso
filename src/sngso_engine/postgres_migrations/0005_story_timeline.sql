-- PostgreSQL migration 0005: story decisions, applications, and timeline events.
-- Ports SQLite registry migrations 5-7 and the review invariants from migration 10.

CREATE TABLE story_decisions (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    operation text NOT NULL CHECK (operation IN ('merge', 'split')),
    canonical_story_id text NOT NULL CHECK (length(btrim(canonical_story_id)) > 0),
    story_ids_json text NOT NULL CHECK (jsonb_typeof(story_ids_json::jsonb) = 'array'),
    article_ids_json text NOT NULL CHECK (jsonb_typeof(article_ids_json::jsonb) = 'array'),
    decided_by text NOT NULL CHECK (length(btrim(decided_by)) > 0),
    rationale text NOT NULL CHECK (length(btrim(rationale)) > 0),
    decided_at text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object')
);
CREATE INDEX idx_story_decisions_decided_at ON story_decisions(decided_at, id);

CREATE FUNCTION reject_story_decision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'story decisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER story_decisions_no_update
BEFORE UPDATE OR DELETE ON story_decisions
FOR EACH ROW EXECUTE FUNCTION reject_story_decision_mutation();

CREATE TABLE story_decision_applications (
    application_id text PRIMARY KEY CHECK (length(btrim(application_id)) > 0),
    decision_id text NOT NULL UNIQUE REFERENCES story_decisions(id) ON DELETE RESTRICT,
    applied_at text NOT NULL,
    actor text NOT NULL CHECK (length(btrim(actor)) BETWEEN 1 AND 200),
    rationale text NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 4000),
    decision_fingerprint text NOT NULL CHECK (decision_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    impact_fingerprint text NOT NULL CHECK (impact_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    result_json text NOT NULL CHECK (jsonb_typeof(result_json::jsonb) = 'object')
);
CREATE INDEX idx_story_decision_applications_applied_at
    ON story_decision_applications(applied_at, application_id);

CREATE FUNCTION reject_story_decision_application_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'story decision applications are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER story_decision_applications_no_update
BEFORE UPDATE OR DELETE ON story_decision_applications
FOR EACH ROW EXECUTE FUNCTION reject_story_decision_application_mutation();

CREATE TABLE timeline_events (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    story_id text NOT NULL,
    occurred_sort_key text,
    precision text NOT NULL CHECK (precision IN ('instant', 'date', 'range', 'approximate', 'unknown')),
    supersedes_event_id text UNIQUE,
    created_at text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (story_id, id),
    FOREIGN KEY (story_id, supersedes_event_id)
        REFERENCES timeline_events(story_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_timeline_events_story_time
    ON timeline_events(story_id, occurred_sort_key, created_at, id);

CREATE FUNCTION validate_timeline_event_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    claim_ids jsonb;
    claim_id text;
    review_status text;
    reviewed_at text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM domain_records
         WHERE record_type = 'story' AND id = NEW.story_id
           AND payload_json::jsonb ->> 'status' IS DISTINCT FROM 'archived'
    ) THEN
        RAISE EXCEPTION 'timeline event requires an active registered story'
            USING ERRCODE = '23514';
    END IF;

    claim_ids := NEW.payload_json::jsonb -> 'claim_ids';
    IF jsonb_typeof(claim_ids) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'timeline event claim_ids must be an array'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(claim_ids) = 0 THEN
        RAISE EXCEPTION 'timeline event requires one or more story claims'
            USING ERRCODE = '23514';
    END IF;
    FOR claim_id IN SELECT jsonb_array_elements_text(claim_ids)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM domain_records
             WHERE record_type = 'claim' AND id = claim_id
               AND payload_json::jsonb ->> 'story_id' = NEW.story_id
        ) THEN
            RAISE EXCEPTION 'timeline event claims must belong to its story'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    review_status := NEW.payload_json::jsonb ->> 'review_status';
    reviewed_at := NEW.payload_json::jsonb ->> 'reviewed_at';
    IF review_status IN ('approved', 'rejected') AND (
        reviewed_at IS NULL
        OR btrim(COALESCE(NEW.payload_json::jsonb ->> 'reviewed_by', '')) = ''
        OR btrim(COALESCE(NEW.payload_json::jsonb ->> 'review_note', '')) = ''
    ) THEN
        RAISE EXCEPTION 'reviewed timeline event requires reviewer, timestamp, and note'
            USING ERRCODE = '23514';
    END IF;
    IF review_status IN ('unreviewed', 'review_required') AND (
        NEW.payload_json::jsonb ? 'reviewed_at'
        OR NEW.payload_json::jsonb ? 'reviewed_by'
        OR NEW.payload_json::jsonb ? 'review_note'
    ) THEN
        RAISE EXCEPTION 'unreviewed timeline event cannot contain review metadata'
            USING ERRCODE = '23514';
    END IF;
    IF review_status IN ('approved', 'rejected')
       AND reviewed_at::timestamptz < NEW.created_at::timestamptz THEN
        RAISE EXCEPTION 'timeline event review cannot predate its creation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timeline_events_validate_insert
BEFORE INSERT ON timeline_events
FOR EACH ROW EXECUTE FUNCTION validate_timeline_event_insert();

CREATE FUNCTION reject_timeline_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'timeline events are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER timeline_events_no_update
BEFORE UPDATE OR DELETE ON timeline_events
FOR EACH ROW EXECUTE FUNCTION reject_timeline_event_mutation();
