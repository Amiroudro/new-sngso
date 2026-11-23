-- PostgreSQL migration 0011: specialized learning evidence provenance.
-- Ports SQLite migrations 24-25. These checks bind learning objects to the
-- exact current evidence revision and the claim's source provenance.

CREATE FUNCTION learning_claim_has_evidence_provenance(claim_payload jsonb, evidence_payload jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF jsonb_typeof(claim_payload -> 'evidence_ids') IS DISTINCT FROM 'array'
       OR jsonb_typeof(claim_payload -> 'provenance') IS DISTINCT FROM 'array' THEN
        RETURN FALSE;
    END IF;
    RETURN EXISTS (
        SELECT 1
        FROM jsonb_array_elements(claim_payload -> 'evidence_ids') AS item(value)
        WHERE item.value = evidence_payload -> 'id'
    ) AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements(claim_payload -> 'provenance') AS provenance(value)
        WHERE provenance.value ->> 'source_id' = evidence_payload ->> 'source_id'
          AND (
              evidence_payload ->> 'article_id' IS NULL
              OR provenance.value ->> 'article_id' = evidence_payload ->> 'article_id'
          )
          AND (
              evidence_payload ->> 'observation_id' IS NULL
              OR provenance.value ->> 'observation_id' = evidence_payload ->> 'observation_id'
          )
    );
END;
$$;

CREATE FUNCTION validate_learning_object_specialized_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    content jsonb;
    claim_id text;
    claim_payload jsonb;
    evidence_payload jsonb;
    source_evidence_id text;
    source_evidence_revision integer;
    answer_ref jsonb;
    answer_claim_id text;
    answer_evidence_id text;
    answer_evidence_revision integer;
    seen_pairs text[];
BEGIN
    payload := NEW.payload_json::jsonb;
    content := payload -> 'content';

    IF NEW.kind = 'vocabulary' THEN
        source_evidence_id := content ->> 'source_evidence_id';
        BEGIN
            source_evidence_revision := (content ->> 'source_evidence_revision')::integer;
        EXCEPTION WHEN OTHERS THEN
            source_evidence_revision := NULL;
        END;

        IF source_evidence_id IS NULL OR source_evidence_revision IS NULL
           OR jsonb_typeof(payload -> 'claim_ids') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'vocabulary source evidence must be current and linked to an object claim'
                USING ERRCODE = '23514';
        END IF;

        FOR claim_id IN SELECT jsonb_array_elements_text(payload -> 'claim_ids')
        LOOP
            SELECT record.payload_json::jsonb
              INTO claim_payload
              FROM domain_records record
             WHERE record.record_type = 'claim' AND record.id = claim_id;

            SELECT record.payload_json::jsonb
              INTO evidence_payload
              FROM domain_records record
             WHERE record.record_type = 'evidence'
               AND record.id = source_evidence_id
               AND record.revision = source_evidence_revision;

            IF claim_payload IS NOT NULL AND evidence_payload IS NOT NULL
               AND evidence_payload ->> 'claim_id' = claim_id
               AND learning_claim_has_evidence_provenance(claim_payload, evidence_payload) THEN
                RETURN NEW;
            END IF;
        END LOOP;

        RAISE EXCEPTION 'vocabulary source evidence must be current and linked to an object claim'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.kind IN ('comprehension_question', 'analysis_question', 'mcq_question') THEN
        IF jsonb_typeof(content -> 'answer_evidence') IS DISTINCT FROM 'array'
           OR jsonb_array_length(content -> 'answer_evidence') = 0
           OR jsonb_typeof(content -> 'answer_claim_ids') IS DISTINCT FROM 'array'
           OR jsonb_typeof(payload -> 'claim_ids') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'question answer evidence is required' USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements(content -> 'answer_evidence') AS refs(value)
             GROUP BY refs.value ->> 'claim_id', refs.value ->> 'evidence_id'
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION 'question answer evidence references must be unique'
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements_text(content -> 'answer_claim_ids') AS answer_claim(value)
             WHERE NOT EXISTS (
                 SELECT 1
                   FROM jsonb_array_elements(content -> 'answer_evidence') AS refs(value)
                  WHERE refs.value ->> 'claim_id' = answer_claim.value
             )
        ) THEN
            RAISE EXCEPTION 'question answer evidence must cover every answer claim'
                USING ERRCODE = '23514';
        END IF;

        FOR answer_ref IN SELECT value FROM jsonb_array_elements(content -> 'answer_evidence')
        LOOP
            answer_claim_id := answer_ref ->> 'claim_id';
            answer_evidence_id := answer_ref ->> 'evidence_id';
            answer_evidence_revision := NULL;
            BEGIN
                answer_evidence_revision := (answer_ref ->> 'evidence_revision')::integer;
            EXCEPTION WHEN OTHERS THEN
                answer_evidence_revision := NULL;
            END;

            IF answer_claim_id IS NULL OR answer_evidence_id IS NULL
               OR answer_evidence_revision IS NULL
               OR NOT (content -> 'answer_claim_ids' @> jsonb_build_array(answer_claim_id))
               OR NOT (payload -> 'claim_ids' @> jsonb_build_array(answer_claim_id)) THEN
                RAISE EXCEPTION 'question answer evidence must be current and linked to its answer claim'
                    USING ERRCODE = '23514';
            END IF;

            SELECT record.payload_json::jsonb
              INTO claim_payload
              FROM domain_records record
             WHERE record.record_type = 'claim' AND record.id = answer_claim_id;

            SELECT record.payload_json::jsonb
              INTO evidence_payload
              FROM domain_records record
             WHERE record.record_type = 'evidence'
               AND record.id = answer_evidence_id
               AND record.revision = answer_evidence_revision;

            IF claim_payload IS NULL OR evidence_payload IS NULL
               OR evidence_payload ->> 'claim_id' IS DISTINCT FROM answer_claim_id
               OR evidence_payload ->> 'relationship' IS DISTINCT FROM 'supports'
               OR evidence_payload #>> '{locator,uri}' IS NULL
               OR NOT learning_claim_has_evidence_provenance(claim_payload, evidence_payload) THEN
                RAISE EXCEPTION 'question answer evidence must be current and linked to its answer claim'
                    USING ERRCODE = '23514';
            END IF;
        END LOOP;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER learning_objects_specialized_evidence_insert
BEFORE INSERT ON learning_objects
FOR EACH ROW EXECUTE FUNCTION validate_learning_object_specialized_evidence();

CREATE TRIGGER learning_objects_specialized_evidence_update
BEFORE UPDATE OF payload_json ON learning_objects
FOR EACH ROW EXECUTE FUNCTION validate_learning_object_specialized_evidence();
