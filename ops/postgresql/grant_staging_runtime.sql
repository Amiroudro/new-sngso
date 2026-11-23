-- Least-privilege role grants for the PostgreSQL staging service.
-- Run with psql as a database administrator or the schema owner after migrations.
-- This script changes grants only; it does not create LOGIN roles or set passwords.
--
-- Usage:
--   psql "$DATABASE_URL" -v app_schema=sngso \
--     -v runtime_role=sngso_staging_runtime -f ops/postgresql/grant_staging_runtime.sql
--
-- The LOGIN role used by the service must be a member of runtime_role only.
-- The service connection must pin search_path to app_schema explicitly.
-- Do not grant it membership in the schema owner or migration role.

\if :{?app_schema}
\else
\set app_schema sngso
\endif
\if :{?runtime_role}
\else
\set runtime_role sngso_staging_runtime
\endif
\if :{?migration_role}
\else
\set migration_role sngso_migrator
\endif

-- Remove any previous direct table grants from this role, then grant the
-- minimum read surface required by the transaction-bound export re-verifier.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA :"app_schema"
    FROM :"runtime_role";
REVOKE CREATE ON SCHEMA :"app_schema" FROM :"runtime_role";
GRANT USAGE ON SCHEMA :"app_schema" TO :"runtime_role";
GRANT SELECT ON ALL TABLES IN SCHEMA :"app_schema" TO :"runtime_role";

-- PostgreSQL requires UPDATE privilege on at least one column of a table
-- selected FOR SHARE/UPDATE. Grant UPDATE on primary-key columns only; the
-- 0023_primary_key_identity_guards migration makes every such identity
-- immutable. The role cannot update non-key editorial/permission/benchmark/
-- pipeline data. Full UPDATE is granted only on the guarded staging and audit
-- state heads below.
SET search_path TO :"app_schema";
SET sngso.runtime_role TO :'runtime_role';

DO $preflight$
BEGIN
    IF to_regprocedure('reject_primary_key_mutation()') IS NULL THEN
        RAISE EXCEPTION 'PostgreSQL migration 0023 primary-key identity guards must be applied before staging role grants';
    END IF;
END;
$preflight$;
DO $grant_pk$
DECLARE
    item record;
    target_schema text := current_schema();
    target_role text := current_setting('sngso.runtime_role');
BEGIN
    FOR item IN
        SELECT kcu.table_name, kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_catalog = tc.constraint_catalog
         AND kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
         AND kcu.table_name = tc.table_name
        WHERE tc.table_schema = target_schema
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.table_name, kcu.ordinal_position
    LOOP
        EXECUTE format(
            'GRANT UPDATE (%I) ON TABLE %I.%I TO %I',
            item.column_name, target_schema, item.table_name, target_role
        );
    END LOOP;
END;
$grant_pk$;

-- The staging service may append immutable staging/audit events and advance
-- the two guarded singleton heads. It has UPDATE on primary-key columns only
-- to take row locks; migration 0023 rejects identity changes. It has no
-- non-key UPDATE on editorial, benchmark, permission, or pipeline data.
GRANT INSERT ON TABLE
    :"app_schema".edition_staging_events,
    :"app_schema".audit_events
TO :"runtime_role";

GRANT UPDATE ON TABLE
    :"app_schema".edition_staging_channel_state,
    :"app_schema".audit_chain_head
TO :"runtime_role";

-- Future tables created by the migration role are readable by this service.
-- Write privileges for new tables must be reviewed and granted explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role"
    IN SCHEMA :"app_schema"
    GRANT SELECT ON TABLES TO :"runtime_role";

-- Deliberately absent:
--   * DELETE or TRUNCATE on any table
--   * UPDATE on non-primary-key columns of editorial, benchmark, permission,
--     or pipeline records; primary-key UPDATE is lock-only and guarded by 0023
--   * CREATE on the application schema
