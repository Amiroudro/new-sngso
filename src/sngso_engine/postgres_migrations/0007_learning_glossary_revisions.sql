-- PostgreSQL migration 0007: learning objects and glossary revision registries.
-- Ports the base tables and revision lifecycle from SQLite migrations 11-12.
-- Specialized MCQ, vocabulary, evidence and bilingual review checks follow separately.

CREATE TABLE learning_objects (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    story_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN (
        'headline_summary', 'brief_summary', 'detailed_summary', 'vocabulary',
        'comprehension_question', 'analysis_question', 'mcq_question', 'revision_card'
    )),
    language text NOT NULL,
    variant_group_id text,
    status text NOT NULL CHECK (status IN ('draft', 'review_required', 'approved', 'rejected')),
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    updated_at text NOT NULL,
    last_actor text NOT NULL CHECK (length(btrim(last_actor)) > 0),
    last_reason text NOT NULL CHECK (length(btrim(last_reason)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (story_id, id)
);
CREATE INDEX idx_learning_objects_story_kind_language
    ON learning_objects(story_id, kind, language, id);
CREATE UNIQUE INDEX idx_learning_objects_variant_language
    ON learning_objects(story_id, kind, variant_group_id, language)
    WHERE variant_group_id IS NOT NULL;

CREATE TABLE learning_object_revisions (
    learning_object_id text NOT NULL REFERENCES learning_objects(id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    PRIMARY KEY (learning_object_id, revision)
);

CREATE FUNCTION validate_learning_object_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    claim_id text;
    review_status text;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF NOT EXISTS (
        SELECT 1 FROM domain_records
         WHERE record_type = 'story' AND id = NEW.story_id
           AND payload_json::jsonb ->> 'status' IS DISTINCT FROM 'archived'
    ) THEN
        RAISE EXCEPTION 'learning object requires an active registered story'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(payload -> 'claim_ids') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'learning object claims must belong to its story'
            USING ERRCODE = '23514';
    END IF;
    FOR claim_id IN SELECT jsonb_array_elements_text(payload -> 'claim_ids')
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM domain_records
             WHERE record_type = 'claim' AND id = claim_id
               AND payload_json::jsonb ->> 'story_id' = NEW.story_id
        ) THEN
            RAISE EXCEPTION 'learning object claims must belong to its story'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('draft', 'review_required') THEN
            RAISE EXCEPTION 'new learning object must start in draft or review-required state'
                USING ERRCODE = '23514';
        END IF;
        IF payload ? 'reviewed_at' OR payload ? 'reviewed_by' OR payload ? 'review_note' THEN
            RAISE EXCEPTION 'new learning object cannot contain review metadata'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.story_id IS DISTINCT FROM OLD.story_id
           OR NEW.kind IS DISTINCT FROM OLD.kind
           OR NEW.language IS DISTINCT FROM OLD.language
           OR NEW.variant_group_id IS DISTINCT FROM OLD.variant_group_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'learning object identity is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'learning object revision must increment by one' USING ERRCODE = '23514';
        END IF;
        IF NEW.updated_at::timestamptz < OLD.updated_at::timestamptz THEN
            RAISE EXCEPTION 'learning object update time cannot move backwards' USING ERRCODE = '23514';
        END IF;
        review_status := NEW.status;
        IF review_status IN ('approved', 'rejected') AND (
            NOT (payload ? 'reviewed_at')
            OR btrim(COALESCE(payload ->> 'reviewed_by', '')) = ''
            OR btrim(COALESCE(payload ->> 'review_note', '')) = ''
        ) THEN
            RAISE EXCEPTION 'reviewed learning object requires reviewer, timestamp, and note'
                USING ERRCODE = '23514';
        END IF;
        IF review_status IN ('approved', 'rejected')
           AND (payload ->> 'reviewed_at')::timestamptz < NEW.created_at::timestamptz THEN
            RAISE EXCEPTION 'learning object review cannot predate its creation'
                USING ERRCODE = '23514';
        END IF;
        IF review_status IN ('draft', 'review_required') AND (
            payload ? 'reviewed_at' OR payload ? 'reviewed_by' OR payload ? 'review_note'
        ) THEN
            RAISE EXCEPTION 'unreviewed learning object cannot contain review metadata'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.variant_group_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM learning_objects existing
         WHERE existing.variant_group_id = NEW.variant_group_id
           AND existing.id IS DISTINCT FROM NEW.id
           AND (existing.story_id <> NEW.story_id OR existing.kind <> NEW.kind)
    ) THEN
        RAISE EXCEPTION 'learning object variants must share the same story and kind'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER learning_objects_validate_write
BEFORE INSERT OR UPDATE ON learning_objects
FOR EACH ROW EXECUTE FUNCTION validate_learning_object_write();

CREATE FUNCTION reject_learning_object_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'learning objects cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER learning_objects_no_delete
BEFORE DELETE ON learning_objects
FOR EACH ROW EXECUTE FUNCTION reject_learning_object_delete();

CREATE FUNCTION validate_learning_object_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM learning_objects
         WHERE id = NEW.learning_object_id
           AND revision = NEW.revision
           AND payload_json = NEW.payload_json
    ) THEN
        RAISE EXCEPTION 'learning object revision must match the current record'
            USING ERRCODE = '23514';
    END IF;
    IF btrim(NEW.actor) = '' OR btrim(NEW.reason) = '' THEN
        RAISE EXCEPTION 'learning object revision requires actor and reason'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER learning_object_revisions_validate_insert
BEFORE INSERT ON learning_object_revisions
FOR EACH ROW EXECUTE FUNCTION validate_learning_object_revision_insert();

CREATE FUNCTION record_learning_object_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO learning_object_revisions
        (learning_object_id, revision, created_at, actor, reason, payload_json)
    VALUES (
        NEW.id, NEW.revision,
        CASE WHEN TG_OP = 'INSERT' THEN NEW.created_at ELSE NEW.updated_at END,
        NEW.last_actor, NEW.last_reason, NEW.payload_json
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER learning_objects_record_revision
AFTER INSERT OR UPDATE ON learning_objects
FOR EACH ROW EXECUTE FUNCTION record_learning_object_revision();

CREATE FUNCTION reject_learning_object_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'learning object revisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER learning_object_revisions_no_update
BEFORE UPDATE OR DELETE ON learning_object_revisions
FOR EACH ROW EXECUTE FUNCTION reject_learning_object_revision_mutation();

CREATE TABLE glossary_entries (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    lemma text NOT NULL,
    lemma_key text NOT NULL,
    term_language text NOT NULL,
    part_of_speech text NOT NULL,
    status text NOT NULL CHECK (status IN ('draft', 'review_required', 'approved', 'rejected')),
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    updated_at text NOT NULL,
    last_actor text NOT NULL CHECK (length(btrim(last_actor)) > 0),
    last_reason text NOT NULL CHECK (length(btrim(last_reason)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (term_language, part_of_speech, lemma_key)
);

CREATE TABLE glossary_entry_revisions (
    glossary_entry_id text NOT NULL REFERENCES glossary_entries(id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    PRIMARY KEY (glossary_entry_id, revision)
);

CREATE FUNCTION validate_glossary_entry_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('draft', 'review_required') THEN
            RAISE EXCEPTION 'new glossary entry must start in draft or review-required state'
                USING ERRCODE = '23514';
        END IF;
        IF payload ? 'reviewed_at' OR payload ? 'reviewed_by' OR payload ? 'review_note' THEN
            RAISE EXCEPTION 'new glossary entry cannot contain review metadata'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.lemma IS DISTINCT FROM OLD.lemma
           OR NEW.lemma_key IS DISTINCT FROM OLD.lemma_key
           OR NEW.term_language IS DISTINCT FROM OLD.term_language
           OR NEW.part_of_speech IS DISTINCT FROM OLD.part_of_speech
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'glossary entry identity is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'glossary entry revision must increment by one' USING ERRCODE = '23514';
        END IF;
        IF btrim(NEW.last_actor) = '' OR btrim(NEW.last_reason) = '' THEN
            RAISE EXCEPTION 'glossary entry revision requires actor and reason'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.updated_at::timestamptz < OLD.updated_at::timestamptz THEN
            RAISE EXCEPTION 'glossary entry update time cannot move backwards'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status IN ('approved', 'rejected') AND (
            NOT (payload ? 'reviewed_at')
            OR btrim(COALESCE(payload ->> 'reviewed_by', '')) = ''
            OR btrim(COALESCE(payload ->> 'review_note', '')) = ''
        ) THEN
            RAISE EXCEPTION 'reviewed glossary entry requires reviewer, timestamp, and note'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status IN ('approved', 'rejected')
           AND (payload ->> 'reviewed_at')::timestamptz < NEW.created_at::timestamptz THEN
            RAISE EXCEPTION 'glossary entry review cannot predate its creation'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status IN ('draft', 'review_required') AND (
            payload ? 'reviewed_at' OR payload ? 'reviewed_by' OR payload ? 'review_note'
        ) THEN
            RAISE EXCEPTION 'unreviewed glossary entry cannot contain review metadata'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER glossary_entries_validate_write
BEFORE INSERT OR UPDATE ON glossary_entries
FOR EACH ROW EXECUTE FUNCTION validate_glossary_entry_write();

CREATE FUNCTION reject_glossary_entry_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'glossary entries cannot be deleted' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER glossary_entries_no_delete
BEFORE DELETE ON glossary_entries
FOR EACH ROW EXECUTE FUNCTION reject_glossary_entry_delete();

CREATE FUNCTION validate_glossary_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM glossary_entries
         WHERE id = NEW.glossary_entry_id
           AND revision = NEW.revision
           AND payload_json = NEW.payload_json
    ) THEN
        RAISE EXCEPTION 'glossary revision must match the current entry'
            USING ERRCODE = '23514';
    END IF;
    IF btrim(NEW.actor) = '' OR btrim(NEW.reason) = '' THEN
        RAISE EXCEPTION 'glossary revision requires actor and reason'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER glossary_entry_revisions_validate_insert
BEFORE INSERT ON glossary_entry_revisions
FOR EACH ROW EXECUTE FUNCTION validate_glossary_revision_insert();

CREATE FUNCTION record_glossary_entry_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO glossary_entry_revisions
        (glossary_entry_id, revision, created_at, actor, reason, payload_json)
    VALUES (
        NEW.id, NEW.revision,
        CASE WHEN TG_OP = 'INSERT' THEN NEW.created_at ELSE NEW.updated_at END,
        NEW.last_actor, NEW.last_reason, NEW.payload_json
    );
    RETURN NEW;
END;
$$;
CREATE TRIGGER glossary_entries_record_revision
AFTER INSERT OR UPDATE ON glossary_entries
FOR EACH ROW EXECUTE FUNCTION record_glossary_entry_revision();

CREATE FUNCTION reject_glossary_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'glossary revisions are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER glossary_entry_revisions_no_update
BEFORE UPDATE OR DELETE ON glossary_entry_revisions
FOR EACH ROW EXECUTE FUNCTION reject_glossary_revision_mutation();
