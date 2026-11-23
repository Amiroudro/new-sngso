# PostgreSQL staging runtime role

Status: **repository grant template and PostgreSQL privilege test implemented; target-environment provisioning and credential validation remain outstanding.**

The staging service does not need broad registry write access. Its transaction-bound
re-verifier reads edition, story, learning, source-rights, benchmark, pipeline,
approval, and release-authorization evidence. The staging service itself writes
only an immutable staging event, an immutable global audit event, and the two
guarded singleton state heads.

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
| `edition_staging_events` | INSERT | Append the immutable promote/rollback event |
| `audit_events` | INSERT | Append the matching global audit event |
| `edition_staging_channel_state` | UPDATE | Advance the guarded channel state by compare-and-swap |
| `audit_chain_head` | UPDATE | Advance the guarded global audit-chain head |

The role receives no DELETE or TRUNCATE privilege, no UPDATE on event/revision
history, no UPDATE on source/permission/editorial/benchmark/pipeline rows, and
no CREATE privilege on the application schema. Trigger guards remain enabled
as defense in depth.

Default privileges grant SELECT only on future tables created by
`sngso_migrator`. Any future staging write surface must be separately reviewed
and explicitly granted. The staging role is not a general PostgreSQL registry
writer; other write services need their own narrowly scoped roles and APIs.

## Automated evidence

The PostgreSQL integration test creates a non-owner role with this same
privilege shape, stages an export through `stage_edition_export()`, and checks
that UPDATE/DELETE/TRUNCATE attempts against event history and unrelated source
records fail with `InsufficientPrivilege`. It also verifies that the permitted
staging and audit writes commit together.

This is a privilege-shape test in an isolated CI schema, not proof that the
target database has been provisioned. Deployment acceptance still requires
reviewing actual role memberships, object ownership, default privileges,
connection identity, secret rotation, and a staging transaction run as the
real service LOGIN role.
