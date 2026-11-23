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

-- The staging service may append immutable staging/audit events and advance
-- the two guarded singleton heads. It cannot UPDATE/DELETE/TRUNCATE event
-- history or mutate any editorial, benchmark, permission, or pipeline row.
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
--   * UPDATE on audit_events or edition_staging_events
--   * UPDATE on editorial, benchmark, permission, or pipeline records
--   * CREATE on the application schema
