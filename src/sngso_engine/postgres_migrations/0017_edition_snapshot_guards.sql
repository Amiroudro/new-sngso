-- PostgreSQL migration 0017: controlled edition snapshot lifecycle.
-- Ports SQLite migration 29. Published editions can only be created by the
-- controlled release workflow; all edition snapshots are immutable.

CREATE FUNCTION reject_uncontrolled_published_edition_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.record_type = 'edition'
       AND NEW.payload_json::jsonb ->> 'status' = 'published' THEN
        RAISE EXCEPTION 'published editions require the controlled release workflow'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_domain_records_no_published_insert
BEFORE INSERT ON domain_records
FOR EACH ROW EXECUTE FUNCTION reject_uncontrolled_published_edition_insert();

CREATE FUNCTION reject_edition_domain_record_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.record_type = 'edition' OR NEW.record_type = 'edition' THEN
        RAISE EXCEPTION 'edition snapshots are immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER edition_domain_records_no_update
BEFORE UPDATE ON domain_records
FOR EACH ROW EXECUTE FUNCTION reject_edition_domain_record_mutation();

CREATE FUNCTION reject_edition_domain_record_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.record_type = 'edition' THEN
        RAISE EXCEPTION 'edition snapshots are immutable' USING ERRCODE = '55000';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER edition_domain_records_no_delete
BEFORE DELETE ON domain_records
FOR EACH ROW EXECUTE FUNCTION reject_edition_domain_record_delete();
