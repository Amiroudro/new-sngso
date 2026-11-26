-- PostgreSQL migration 0020: final revision and workflow guards.
-- Ports the validation and transition guards from SQLite migration 34.

CREATE FUNCTION validate_domain_record_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM domain_records record
         WHERE record.record_type = NEW.record_type
           AND record.id = NEW.record_id
           AND record.revision = NEW.revision
           AND record.payload_json = NEW.payload_json
    ) OR btrim(NEW.actor) = '' OR btrim(NEW.reason) = '' THEN
        RAISE EXCEPTION 'domain revision must match current head and include actor/reason'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER domain_record_revisions_validate_insert
BEFORE INSERT ON domain_record_revisions
FOR EACH ROW EXECUTE FUNCTION validate_domain_record_revision_insert();

CREATE FUNCTION validate_permission_grant_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    current_grant permission_grants%ROWTYPE;
    next_status text;
BEGIN
    payload := NEW.payload_json::jsonb;
    SELECT * INTO current_grant FROM permission_grants WHERE id = NEW.grant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'grant revision must match current or next legal head and include actor/reason'
            USING ERRCODE = '23514';
    END IF;
    next_status := payload ->> 'status';
    IF NOT (
        (current_grant.revision = NEW.revision AND current_grant.payload_json = NEW.payload_json)
        OR (
            current_grant.revision + 1 = NEW.revision
            AND payload ->> 'id' = current_grant.id
            AND payload ->> 'source_id' = current_grant.source_id
            AND (
                (current_grant.status = 'pending' AND next_status IN ('approved', 'denied', 'suspended', 'revoked'))
                OR (current_grant.status = 'approved' AND next_status IN ('suspended', 'revoked'))
                OR (current_grant.status = 'suspended' AND next_status IN ('approved', 'revoked'))
            )
        )
    ) OR btrim(NEW.actor) = '' OR btrim(NEW.reason) = '' THEN
        RAISE EXCEPTION 'grant revision must match current or next legal head and include actor/reason'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER permission_grant_revisions_validate_insert
BEFORE INSERT ON permission_grant_revisions
FOR EACH ROW EXECUTE FUNCTION validate_permission_grant_revision_insert();

CREATE FUNCTION validate_permission_grant_revision_update()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    new_payload jsonb;
    old_payload jsonb;
BEGIN
    new_payload := NEW.payload_json::jsonb;
    old_payload := OLD.payload_json::jsonb;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.source_id IS DISTINCT FROM OLD.source_id
       OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
       OR NEW.effective_until IS DISTINCT FROM OLD.effective_until
       OR NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'permission grant identity is immutable and revision must increment by one'
            USING ERRCODE = '23514';
    END IF;
    IF new_payload ->> 'id' IS DISTINCT FROM NEW.id
       OR new_payload ->> 'source_id' IS DISTINCT FROM NEW.source_id
       OR new_payload ->> 'status' IS DISTINCT FROM NEW.status THEN
        RAISE EXCEPTION 'permission grant payload does not match indexed fields'
            USING ERRCODE = '23514';
    END IF;
    IF (new_payload - ARRAY[
        'status', 'reviewed_at', 'reviewed_by', 'revoked_at',
        'revocation_reason', 'notes'
    ]) IS DISTINCT FROM (old_payload - ARRAY[
        'status', 'reviewed_at', 'reviewed_by', 'revoked_at',
        'revocation_reason', 'notes'
    ])::jsonb THEN
        RAISE EXCEPTION 'permission grant scope and evidence are immutable across status transitions'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM permission_grant_revisions revision
         WHERE revision.grant_id = NEW.id
           AND revision.revision = NEW.revision
           AND revision.payload_json = NEW.payload_json
    ) THEN
        RAISE EXCEPTION 'permission grant update requires matching revision'
            USING ERRCODE = '23514';
    END IF;
    IF NOT (
        (OLD.status = 'pending' AND NEW.status IN ('approved', 'denied', 'suspended', 'revoked'))
        OR (OLD.status = 'approved' AND NEW.status IN ('suspended', 'revoked'))
        OR (OLD.status = 'suspended' AND NEW.status IN ('approved', 'revoked'))
    ) THEN
        RAISE EXCEPTION 'invalid permission grant status transition'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER permission_grants_revision_guard
BEFORE UPDATE ON permission_grants
FOR EACH ROW EXECUTE FUNCTION validate_permission_grant_revision_update();

CREATE FUNCTION validate_benchmark_review_assignment_revision_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    assignment benchmark_review_assignments%ROWTYPE;
    next_status text;
BEGIN
    payload := NEW.payload_json::jsonb;
    SELECT * INTO assignment
      FROM benchmark_review_assignments
     WHERE assignment_id = NEW.assignment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'assignment revision must match current or next legal head and include actor/reason'
            USING ERRCODE = '23514';
    END IF;
    next_status := payload ->> 'status';
    IF NOT (
        (assignment.revision = NEW.revision AND assignment.payload_json = NEW.payload_json)
        OR (
            assignment.revision + 1 = NEW.revision
            AND payload ->> 'assignment_id' = assignment.assignment_id
            AND payload ->> 'artifact_id' = assignment.artifact_id
            AND payload ->> 'artifact_fingerprint' = assignment.artifact_fingerprint
            AND payload ->> 'reviewer_id' = assignment.reviewer_id
            AND payload ->> 'assigned_by' = assignment.assigned_by
            AND payload ->> 'language' = assignment.language
            AND (
                (assignment.status = 'assigned' AND next_status IN ('accepted', 'cancelled', 'expired'))
                OR (assignment.status = 'accepted' AND next_status IN ('submitted', 'cancelled', 'expired'))
            )
        )
    ) OR btrim(NEW.actor) = '' OR btrim(NEW.reason) = '' THEN
        RAISE EXCEPTION 'assignment revision must match current or next legal head and include actor/reason'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_review_assignment_revisions_validate_insert
BEFORE INSERT ON benchmark_review_assignment_revisions
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_review_assignment_revision_insert();

CREATE FUNCTION validate_benchmark_review_assignment_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb;
    reviewer_qualified boolean;
    curator_qualified boolean;
    review_matches boolean;
    accepted_at timestamptz;
    closed_at timestamptz;
BEGIN
    payload := NEW.payload_json::jsonb;
    IF NEW.status IS DISTINCT FROM OLD.status AND (
        btrim(NEW.last_actor) = '' OR btrim(NEW.last_reason) = ''
        OR payload #>> '{last_transition,actor}' IS DISTINCT FROM NEW.last_actor
        OR payload #>> '{last_transition,reason}' IS DISTINCT FROM NEW.last_reason
    ) THEN
        RAISE EXCEPTION 'assignment transition receipt must match actor and reason'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status = 'assigned' AND NEW.status IN ('accepted', 'cancelled', 'expired'))
        OR (OLD.status = 'accepted' AND NEW.status IN ('submitted', 'cancelled', 'expired'))
    ) THEN
        RAISE EXCEPTION 'invalid benchmark assignment status transition'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('accepted', 'submitted') AND NEW.last_actor IS DISTINCT FROM OLD.reviewer_id THEN
        RAISE EXCEPTION 'reviewer must perform accept and submit transitions'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'cancelled' AND NEW.last_actor NOT IN (OLD.reviewer_id, OLD.assigned_by) THEN
        RAISE EXCEPTION 'only reviewer or assigner may cancel assignment'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'expired' AND NEW.last_actor IS DISTINCT FROM OLD.assigned_by THEN
        RAISE EXCEPTION 'only assigner may expire assignment'
            USING ERRCODE = '23514';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM benchmark_principals principal
         WHERE principal.principal_id = NEW.last_actor AND principal.active = 1
           AND principal.roles_json::jsonb @> '["benchmark_reviewer"]'::jsonb
           AND principal.languages_json::jsonb @> jsonb_build_array(NEW.language)
    ) INTO reviewer_qualified;
    SELECT EXISTS (
        SELECT 1 FROM benchmark_principals principal
         WHERE principal.principal_id = NEW.last_actor AND principal.active = 1
           AND principal.roles_json::jsonb @> '["benchmark_curator"]'::jsonb
    ) INTO curator_qualified;

    IF NEW.status IN ('accepted', 'submitted') AND NOT reviewer_qualified THEN
        RAISE EXCEPTION 'assignment reviewer must be active and language-qualified'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('cancelled', 'expired')
       AND NEW.last_actor = OLD.assigned_by AND NOT curator_qualified THEN
        RAISE EXCEPTION 'assignment curator must be active and qualified'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'cancelled' AND NEW.last_actor = OLD.reviewer_id
       AND NOT reviewer_qualified THEN
        RAISE EXCEPTION 'assignment reviewer must be active and language-qualified'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.status = 'accepted' THEN
        IF payload ->> 'accepted_at' IS NULL
           OR (payload ->> 'accepted_at')::timestamptz >= OLD.due_at::timestamptz THEN
            RAISE EXCEPTION 'assignment acceptance must occur before deadline'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.status = 'submitted' THEN
        accepted_at := NULLIF(payload ->> 'accepted_at', '')::timestamptz;
        closed_at := NULLIF(payload ->> 'closed_at', '')::timestamptz;
        SELECT EXISTS (
            SELECT 1 FROM benchmark_reviews review
             WHERE review.review_id = payload ->> 'submitted_review_id'
               AND review.benchmark_id = OLD.artifact_id
               AND review.benchmark_fingerprint = OLD.artifact_fingerprint
               AND review.language = OLD.language
               AND review.payload_json::jsonb #>> '{reviewer,reviewer_id}' = OLD.reviewer_id
        ) INTO review_matches;
        IF accepted_at IS NULL OR closed_at IS NULL
           OR payload ->> 'submitted_review_id' IS NULL
           OR closed_at >= OLD.due_at::timestamptz
           OR closed_at < accepted_at
           OR NOT review_matches THEN
            RAISE EXCEPTION 'submitted assignment requires a timely review for its exact assignee and artifact'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.status IN ('cancelled', 'expired')
       AND (payload ->> 'closed_at' IS NULL
            OR length(btrim(COALESCE(payload ->> 'closure_reason', ''))) = 0) THEN
        RAISE EXCEPTION 'closed assignment requires timestamp and reason'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'expired'
       AND (payload ->> 'closed_at')::timestamptz < OLD.due_at::timestamptz THEN
        RAISE EXCEPTION 'assignment cannot expire before its deadline'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'cancelled' AND OLD.status = 'accepted'
       AND (payload ->> 'closed_at')::timestamptz
           < (OLD.payload_json::jsonb ->> 'accepted_at')::timestamptz THEN
        RAISE EXCEPTION 'assignment cancellation cannot predate acceptance'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER benchmark_review_assignment_status_transition_guard
BEFORE UPDATE ON benchmark_review_assignments
FOR EACH ROW EXECUTE FUNCTION validate_benchmark_review_assignment_transition();
