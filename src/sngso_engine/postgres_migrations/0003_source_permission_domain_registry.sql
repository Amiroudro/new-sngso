-- PostgreSQL migration 0003: foundational source-permission and domain registry.
-- This is the first dependency layer of the SQLite v36 registry port.
-- Text timestamps are retained to preserve the registry's canonical wire values.

CREATE TABLE sources (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    publisher_name text NOT NULL CHECK (length(btrim(publisher_name)) > 0),
    status text NOT NULL CHECK (length(btrim(status)) > 0),
    created_at text NOT NULL CHECK (length(btrim(created_at)) > 0),
    updated_at text NOT NULL CHECK (length(btrim(updated_at)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object')
);

CREATE TABLE source_endpoints (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    source_id text NOT NULL,
    endpoint_uri text NOT NULL CHECK (length(btrim(endpoint_uri)) > 0),
    status text NOT NULL CHECK (length(btrim(status)) > 0),
    created_at text NOT NULL CHECK (length(btrim(created_at)) > 0),
    updated_at text NOT NULL CHECK (length(btrim(updated_at)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (source_id, id),
    UNIQUE (id, source_id),
    UNIQUE (source_id, endpoint_uri),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
);

CREATE TABLE permission_evidence (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    source_id text NOT NULL,
    review_status text NOT NULL CHECK (length(btrim(review_status)) > 0),
    content_fingerprint text NOT NULL CHECK (length(btrim(content_fingerprint)) > 0),
    captured_at text NOT NULL CHECK (length(btrim(captured_at)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (source_id, id),
    UNIQUE (id, source_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
);

CREATE TABLE permission_grants (
    id text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    source_id text NOT NULL,
    status text NOT NULL CHECK (length(btrim(status)) > 0),
    effective_from text NOT NULL CHECK (length(btrim(effective_from)) > 0),
    effective_until text NOT NULL CHECK (length(btrim(effective_until)) > 0),
    revision integer NOT NULL CHECK (revision >= 1),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    UNIQUE (source_id, id),
    UNIQUE (id, source_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
);

CREATE TABLE grant_endpoints (
    grant_id text NOT NULL,
    source_id text NOT NULL,
    endpoint_id text NOT NULL,
    PRIMARY KEY (grant_id, endpoint_id),
    FOREIGN KEY (grant_id, source_id)
        REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT,
    FOREIGN KEY (endpoint_id, source_id)
        REFERENCES source_endpoints(id, source_id) ON DELETE RESTRICT
);

CREATE TABLE grant_evidence (
    grant_id text NOT NULL,
    source_id text NOT NULL,
    evidence_id text NOT NULL,
    PRIMARY KEY (grant_id, evidence_id),
    UNIQUE (grant_id, source_id, evidence_id),
    FOREIGN KEY (grant_id, source_id)
        REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT,
    FOREIGN KEY (evidence_id, source_id)
        REFERENCES permission_evidence(id, source_id) ON DELETE RESTRICT
);

CREATE TABLE grant_operation_evidence (
    grant_id text NOT NULL,
    source_id text NOT NULL,
    operation text NOT NULL CHECK (length(btrim(operation)) > 0),
    evidence_id text NOT NULL,
    PRIMARY KEY (grant_id, operation, evidence_id),
    FOREIGN KEY (grant_id, source_id, evidence_id)
        REFERENCES grant_evidence(grant_id, source_id, evidence_id) ON DELETE RESTRICT
);

CREATE TABLE permission_grant_revisions (
    grant_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL CHECK (length(btrim(created_at)) > 0),
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    PRIMARY KEY (grant_id, revision),
    FOREIGN KEY (grant_id) REFERENCES permission_grants(id) ON DELETE RESTRICT
);

CREATE TABLE domain_records (
    record_type text NOT NULL CHECK (record_type IN ('article', 'story', 'claim', 'evidence', 'edition')),
    id text NOT NULL CHECK (length(btrim(id)) > 0),
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL CHECK (length(btrim(created_at)) > 0),
    updated_at text NOT NULL CHECK (length(btrim(updated_at)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    last_actor text NOT NULL DEFAULT 'migration' CHECK (length(btrim(last_actor)) > 0),
    last_reason text NOT NULL DEFAULT 'schema migration' CHECK (length(btrim(last_reason)) > 0),
    last_revision_at text NOT NULL DEFAULT '1970-01-01T00:00:00Z'
        CHECK (length(btrim(last_revision_at)) > 0),
    PRIMARY KEY (record_type, id)
);

CREATE TABLE domain_record_revisions (
    record_type text NOT NULL,
    record_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at text NOT NULL CHECK (length(btrim(created_at)) > 0),
    actor text NOT NULL CHECK (length(btrim(actor)) > 0),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    payload_json text NOT NULL CHECK (jsonb_typeof(payload_json::jsonb) = 'object'),
    PRIMARY KEY (record_type, record_id, revision),
    FOREIGN KEY (record_type, record_id)
        REFERENCES domain_records(record_type, id) ON DELETE RESTRICT
);

CREATE INDEX idx_endpoints_source ON source_endpoints(source_id);
CREATE INDEX idx_evidence_source ON permission_evidence(source_id, review_status);
CREATE INDEX idx_grants_source_status ON permission_grants(source_id, status);

CREATE FUNCTION reject_permission_grant_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'permission grant revisions are append-only'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER permission_grant_revisions_no_update
BEFORE UPDATE OR DELETE ON permission_grant_revisions
FOR EACH ROW EXECUTE FUNCTION reject_permission_grant_revision_mutation();

CREATE FUNCTION reject_domain_record_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'domain record revisions are append-only'
        USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER domain_record_revisions_no_update
BEFORE UPDATE OR DELETE ON domain_record_revisions
FOR EACH ROW EXECUTE FUNCTION reject_domain_record_revision_mutation();
