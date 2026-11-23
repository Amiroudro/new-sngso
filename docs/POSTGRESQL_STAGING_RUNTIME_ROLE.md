# PostgreSQL staging runtime role

Status: **repository grant template and PostgreSQL privilege test implemented; target-environment provisioning and credential validation remain outstanding.**

The staging service does not need broad registry write access. Its transaction-bound
re-verifier reads edition, story, learning, source-rights, benchmark, pipeline,
approval, and release-authorization evidence. The staging service itself writes
only an immutable staging event, an immutable global audit event, and the two
guarded singleton state heads. PostgreSQL additionally requires UPDATE
privilege on at least one column for each table read with `FOR SHARE` or
`FOR UPDATE`. Migration 0023 installs primary-key immutability guards across
the schema; the runtime role receives UPDATE on primary-key columns only as
lock-enabling privilege, never on non-key editorial or evidence fields.

## Role split

Use separate roles in the deployment database:

- `sngso_migrator`: NOLOGIN schema/table owner used only for reviewed migrations.
- `sngso_staging_runtime`: NOLOGIN group role with the narrow grants below.
- A deployment-managed LOGIN role for the staging process, granted membership
  in `sngso_staging_runtime` only.

Do not grant the staging LOGIN role membership in `sngso_migrator`, the schema
owner, or a broad application writer role. Store login credentials in the
deployment secret manager. The repository does not create or store passwords.
The application connection must explicitly pin its `search_path` to the
application schema (for example, with libpq `options=-c search_path=sngso`);
do not rely on the default `$user, public` path or assume a role switch pins it.

Example role bootstrap, executed by the database administrator after creating
the dedicated application schema:

```sql
CREATE ROLE sngso_migrator NOLOGIN;
CREATE ROLE sngso_staging_runtime NOLOGIN;
CREATE SCHEMA sngso AUTHORIZATION sngso_migrator;
-- Create the LOGIN role through the deployment's secret-management process.
-- Then grant only the group role:
GRANT sngso_staging_runtime TO sngso_staging_login;
```

If roles or schema already exist, inspect their ownership and memberships rather
than blindly rerunning these statements.

## Apply grants

Run the reviewed grant template after migrations:

```sh
psql "$DATABASE_URL" \
  -v app_schema=sngso \
  -v runtime_role=sngso_staging_runtime \
  -v migration_role=sngso_migrator \
  -f ops/postgresql/grant_staging_runtime.sql
```

The template grants:

| Object | Staging runtime privilege | Reason |
|---|---|---|
| All current tables in the application schema | SELECT | Transaction-bound edition and provenance re-verification |
| Primary-key columns across current tables | UPDATE on key columns only | Satisfy PostgreSQL row-lock privilege requirements; migration 0023 rejects key changes |
| `edition_staging_events` | INSERT | Append the immutable promote/rollback event |
| `audit_events` | INSERT | Append the matching global audit event |
| `edition_staging_channel_state` | UPDATE | Advance the guarded channel state by compare-and-swap |
| `audit_chain_head` | UPDATE | Advance the guarded global audit-chain head |

The role receives no DELETE or TRUNCATE privilege, no UPDATE on non-key event/
revision/history or source/permission/editorial/benchmark/pipeline fields, and
no CREATE privilege on the application schema. Key-column UPDATE attempts are
rejected by the primary-key immutability trigger; the audit/staging event tables
also retain their append-only guards. Trigger guards remain enabled
as defense in depth.

Default privileges grant SELECT only on future tables created by
`sngso_migrator`. Every future migration that adds a primary-key-bearing table
must also install the 0023 primary-key guard on that table, and the grant script
must be rerun after the migration so the new key columns receive lock-only
UPDATE. Any future staging write surface must be separately reviewed and
explicitly granted. The staging role is not a general PostgreSQL registry
writer; other write services need their own narrowly scoped roles and APIs.

## Automated evidence

The PostgreSQL integration test creates a non-owner role with this same
privilege shape, takes `FOR SHARE` locks against every table in the isolated
schema, stages an export through `stage_edition_export()`, and checks that
UPDATE/DELETE/TRUNCATE attempts against non-key event fields and unrelated
source fields fail with `InsufficientPrivilege`; attempts to change protected
primary keys fail with an integrity error. It also verifies that the permitted
staging and audit writes commit together.

This is a privilege-shape test in an isolated CI schema, not proof that the
target database has been provisioned. Deployment acceptance still requires
reviewing actual role memberships, object ownership, default privileges,
connection identity, secret rotation, and a staging transaction run as the
real service LOGIN role.
