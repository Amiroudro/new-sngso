-- PostgreSQL migration 0006: perspectives and claim relationships.
-- Ports SQLite registry migrations 8-9, preserving source/story provenance and review rules.

CREATE TABLE perspectives (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    story_id text NOT NULL,
    source_id text REFERENCES sources(id) ON DELETE RESTRICT,
    article_id text,
    supersedes_perspective_id text UNIQUE,
    created_at text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (story_id, id),
    FOREIGN KEY (story_id, supersedes_perspective_id)
        REFERENCES perspectives(story_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_perspectives_story_created
    ON perspectives(story_id, created_at, id);

CREATE FUNCTION validate_perspective_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    story_payload jsonb;
    review_status text;
    claim_id text;
BEGIN
    payload := NEW.payload_json::jsonb;
    SELECT payload_json::jsonb INTO story_payload
      FROM domain_records
     WHERE record_type = 'story' AND id = NEW.story_id
       AND payload_json::jsonb ->> 'status' IS DISTINCT FROM 'archived';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'perspective requires an active registered story'
            USING ERRCODE = '23514';
    END IF;

    IF payload ->> 'kind' = 'publisher_framing'
       AND (NEW.source_id IS NULL OR NEW.article_id IS NULL) THEN
        RAISE EXCEPTION 'publisher framing requires its source and article'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.article_id IS NOT NULL AND (
        NOT EXISTS (
            SELECT 1 FROM domain_records article
             WHERE article.record_type = 'article'
               AND article.id = NEW.article_id
               AND article.payload_json::jsonb ->> 'source_id' = NEW.source_id
        )
        OR NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(
                  CASE WHEN jsonb_typeof(story_payload -> 'article_ids') = 'array'
                       THEN story_payload -> 'article_ids' ELSE '[]'::jsonb END
              ) AS member(article_id)
             WHERE member.article_id = NEW.article_id
        )
    ) THEN
        RAISE EXCEPTION 'perspective article must belong to its source and story'
            USING ERRCODE = '23514';
    END IF;

    review_status := payload ->> 'review_status';
    IF review_status IN ('approved', 'rejected') AND (
        NOT (payload ? 'reviewed_at')
        OR btrim(COALESCE(payload ->> 'reviewed_by', '')) = ''
        OR btrim(COALESCE(payload ->> 'review_note', '')) = ''
    ) THEN
        RAISE EXCEPTION 'reviewed perspective requires reviewer, timestamp, and note'
            USING ERRCODE = '23514';
    END IF;
    IF review_status IN ('unreviewed', 'review_required') AND (
        payload ? 'reviewed_at' OR payload ? 'reviewed_by' OR payload ? 'review_note'
    ) THEN
        RAISE EXCEPTION 'unreviewed perspective cannot contain review metadata'
            USING ERRCODE = '23514';
    END IF;

    IF jsonb_typeof(payload -> 'claim_ids') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'perspective claims must be an array belonging to its story'
            USING ERRCODE = '23514';
    END IF;
    FOR claim_id IN SELECT jsonb_array_elements_text(payload -> 'claim_ids')
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM domain_records
             WHERE record_type = 'claim' AND id = claim_id
               AND payload_json::jsonb ->> 'story_id' = NEW.story_id
        ) THEN
            RAISE EXCEPTION 'perspective claims must belong to its story'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;
CREATE TRIGGER perspectives_validate_insert
BEFORE INSERT ON perspectives
FOR EACH ROW EXECUTE FUNCTION validate_perspective_insert();

CREATE FUNCTION reject_perspective_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'perspectives are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER perspectives_no_update
BEFORE UPDATE OR DELETE ON perspectives
FOR EACH ROW EXECUTE FUNCTION reject_perspective_mutation();

CREATE TABLE claim_relations (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    story_id text NOT NULL,
    claim_id text NOT NULL,
    related_claim_id text NOT NULL,
    relationship text NOT NULL CHECK (relationship IN ('contradicts', 'qualifies', 'narrows', 'reframes')),
    supersedes_relation_id text UNIQUE,
    created_at text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (story_id, id),
    FOREIGN KEY (story_id, supersedes_relation_id)
        REFERENCES claim_relations(story_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_claim_relations_story_created
    ON claim_relations(story_id, created_at, id);

CREATE FUNCTION validate_claim_relation_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    review_status text;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF NOT EXISTS (
        SELECT 1 FROM domain_records
         WHERE record_type = 'story' AND id = NEW.story_id
           AND payload_json::jsonb ->> 'status' IS DISTINCT FROM 'archived'
    ) THEN
        RAISE EXCEPTION 'claim relation requires an active registered story'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.claim_id = NEW.related_claim_id THEN
        RAISE EXCEPTION 'claim relation must connect two different claims'
            USING ERRCODE = '23514';
    END IF;
    IF (
        SELECT count(*) FROM domain_records
         WHERE record_type = 'claim'
           AND id IN (NEW.claim_id, NEW.related_claim_id)
           AND payload_json::jsonb ->> 'story_id' = NEW.story_id
    ) <> 2 THEN
        RAISE EXCEPTION 'claim relation endpoints must belong to its story'
            USING ERRCODE = '23514';
    END IF;

    review_status := payload ->> 'review_status';
    IF review_status IN ('accepted', 'rejected') AND (
        NOT (payload ? 'reviewed_at')
        OR btrim(COALESCE(payload ->> 'reviewed_by', '')) = ''
        OR btrim(COALESCE(payload ->> 'review_note', '')) = ''
    ) THEN
        RAISE EXCEPTION 'reviewed claim relation requires reviewer, timestamp, and note'
            USING ERRCODE = '23514';
    END IF;
    IF review_status = 'unreviewed' AND (
        payload ? 'reviewed_at' OR payload ? 'reviewed_by' OR payload ? 'review_note'
    ) THEN
        RAISE EXCEPTION 'unreviewed claim relation cannot contain review metadata'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER claim_relations_validate_insert
BEFORE INSERT ON claim_relations
FOR EACH ROW EXECUTE FUNCTION validate_claim_relation_insert();

CREATE FUNCTION reject_claim_relation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'claim relations are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER claim_relations_no_update
BEFORE UPDATE OR DELETE ON claim_relations
FOR EACH ROW EXECUTE FUNCTION reject_claim_relation_mutation();
