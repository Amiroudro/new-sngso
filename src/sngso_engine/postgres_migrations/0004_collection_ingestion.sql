-- PostgreSQL migration 0004: collection observations and ingestion decisions.
-- Mirrors SQLite migrations 2-4, including the later GET-only method constraint.

CREATE TABLE collection_observations (
    observation_id text PRIMARY KEY CHECK (length(btrim(observation_id)) > 0),
    source_id text NOT NULL,
    endpoint_id text NOT NULL,
    grant_id text NOT NULL,
    grant_revision integer NOT NULL CHECK (grant_revision >= 1),
    operation text NOT NULL CHECK (operation IN ('discovery', 'metadata')),
    connector_name text NOT NULL CHECK (length(btrim(connector_name)) > 0),
    connector_version text NOT NULL CHECK (length(btrim(connector_version)) > 0),
    observed_at text NOT NULL CHECK (length(btrim(observed_at)) > 0),
    locator text NOT NULL CHECK (length(btrim(locator)) > 0),
    final_locator text,
    content_fingerprint text,
    response_status integer CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
    content_type text,
    metadata_json text NOT NULL CHECK (jsonb_typeof(metadata_json::jsonb) = 'object'),
    permission_reason text NOT NULL CHECK (length(btrim(permission_reason)) > 0),
    http_method text NOT NULL DEFAULT 'GET' CHECK (http_method = 'GET'),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    FOREIGN KEY (endpoint_id, source_id)
        REFERENCES source_endpoints(id, source_id) ON DELETE RESTRICT,
    FOREIGN KEY (grant_id, source_id)
        REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT
);

CREATE INDEX idx_collection_observations_source_time
    ON collection_observations(source_id, observed_at);
CREATE INDEX idx_collection_observations_endpoint_time
    ON collection_observations(endpoint_id, observed_at);

CREATE FUNCTION reject_collection_observation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'collection observations are append-only'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER collection_observations_no_update
BEFORE UPDATE OR DELETE ON collection_observations
FOR EACH ROW EXECUTE FUNCTION reject_collection_observation_mutation();

CREATE TABLE ingestion_decisions (
    decision_id text PRIMARY KEY CHECK (length(btrim(decision_id)) > 0),
    observation_id text NOT NULL,
    decision_revision integer NOT NULL CHECK (decision_revision >= 1),
    source_id text NOT NULL,
    article_id text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN (
        'created', 'reused', 'possible_duplicate', 'confirmed_duplicate', 'distinct'
    )),
    duplicate_of_article_id text,
    fingerprint text CHECK (
        fingerprint IS NULL OR fingerprint ~ '^sha256:[0-9a-f]{64}$'
    ),
    canonical_uri text NOT NULL CHECK (length(btrim(canonical_uri)) > 0),
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
    decided_at text NOT NULL CHECK (length(btrim(decided_at)) > 0),
    UNIQUE (observation_id, decision_revision),
    UNIQUE (observation_id, decision_id),
    FOREIGN KEY (observation_id) REFERENCES collection_observations(observation_id) ON DELETE RESTRICT,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
);

CREATE TABLE ingestion_current (
    observation_id text PRIMARY KEY,
    decision_id text NOT NULL UNIQUE,
    FOREIGN KEY (observation_id) REFERENCES collection_observations(observation_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_id, decision_id)
        REFERENCES ingestion_decisions(observation_id, decision_id) ON DELETE RESTRICT
);

CREATE INDEX idx_ingestion_fingerprint
    ON ingestion_decisions(fingerprint, source_id, decided_at);
CREATE INDEX idx_article_source_uri
    ON domain_records ((payload_json::jsonb ->> 'source_id'),
                       (payload_json::jsonb ->> 'canonical_uri'))
    WHERE record_type = 'article';

CREATE FUNCTION validate_ingestion_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    article_payload jsonb;
    duplicate_payload jsonb;
    observation_source text;
BEGIN
    SELECT payload_json::jsonb INTO article_payload
      FROM domain_records
     WHERE record_type = 'article' AND id = NEW.article_id;

    IF NOT FOUND
       OR article_payload ->> 'source_id' IS DISTINCT FROM NEW.source_id
       OR article_payload ->> 'canonical_uri' IS DISTINCT FROM NEW.canonical_uri THEN
        RAISE EXCEPTION 'ingestion decision article/source/URI mismatch'
            USING ERRCODE = '23514';
    END IF;

    SELECT source_id INTO observation_source
      FROM collection_observations
     WHERE observation_id = NEW.observation_id;

    IF NOT FOUND OR observation_source IS DISTINCT FROM NEW.source_id THEN
        RAISE EXCEPTION 'ingestion decision observation/source mismatch'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.duplicate_of_article_id IS NOT NULL THEN
        SELECT payload_json::jsonb INTO duplicate_payload
          FROM domain_records
         WHERE record_type = 'article' AND id = NEW.duplicate_of_article_id;

        IF NOT FOUND OR duplicate_payload ->> 'source_id' IS NULL
           OR duplicate_payload ->> 'source_id' = NEW.source_id THEN
            RAISE EXCEPTION 'ingestion duplicate target must be a different source article'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
CREATE TRIGGER ingestion_decisions_validate_article
BEFORE INSERT ON ingestion_decisions
FOR EACH ROW EXECUTE FUNCTION validate_ingestion_decision();

CREATE FUNCTION reject_ingestion_decision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ingestion decisions are append-only'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER ingestion_decisions_no_update
BEFORE UPDATE OR DELETE ON ingestion_decisions
FOR EACH ROW EXECUTE FUNCTION reject_ingestion_decision_mutation();

CREATE FUNCTION reject_ingestion_current_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'current ingestion state cannot be deleted'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER ingestion_current_no_delete
BEFORE DELETE ON ingestion_current
FOR EACH ROW EXECUTE FUNCTION reject_ingestion_current_delete();
