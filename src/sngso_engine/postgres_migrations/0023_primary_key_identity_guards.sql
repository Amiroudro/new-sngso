-- PostgreSQL migration 0023: immutable primary-key identity guards.
--
-- PostgreSQL requires UPDATE privilege on at least one column of a row source
-- used by SELECT ... FOR SHARE/UPDATE. The staging/re-verification service
-- therefore receives column-level UPDATE on primary-key columns only. This
-- trigger makes those lock-only grants safe: identities cannot be rewritten.
CREATE FUNCTION reject_primary_key_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    key_column text;
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
BEGIN
    FOREACH key_column IN ARRAY TG_ARGV LOOP
        IF old_row -> key_column IS DISTINCT FROM new_row -> key_column THEN
            RAISE EXCEPTION 'primary-key identity column % is immutable on %',
                key_column, TG_TABLE_NAME
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

DO $$
DECLARE
    item record;
    key_columns text;
    trigger_arguments text;
    target_schema text := current_schema();
BEGIN
    FOR item IN
        SELECT tc.table_name,
               array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS columns
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_catalog = tc.constraint_catalog
         AND kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
         AND kcu.table_name = tc.table_name
        WHERE tc.table_schema = target_schema
          AND tc.constraint_type = 'PRIMARY KEY'
        GROUP BY tc.table_name
        ORDER BY tc.table_name
    LOOP
        SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal)
          INTO key_columns
          FROM unnest(item.columns) WITH ORDINALITY AS keys(column_name, ordinal);
        SELECT string_agg(format('%L', column_name), ', ' ORDER BY ordinal)
          INTO trigger_arguments
          FROM unnest(item.columns) WITH ORDINALITY AS keys(column_name, ordinal);

        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OF %s ON %I.%I '
            'FOR EACH ROW EXECUTE FUNCTION reject_primary_key_mutation(%s)',
            'sngso_primary_key_immutable',
            key_columns,
            target_schema,
            item.table_name,
            trigger_arguments
        );
    END LOOP;
END;
$$;
