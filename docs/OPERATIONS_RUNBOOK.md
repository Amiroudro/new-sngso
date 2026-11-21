# Engine Operations Runbook (Local / Pre-production)

Status: initial operator guidance for the Python engine. This is not a production incident-response plan or evidence of an operating service.

## Scope and safety

- The engine has no scheduled production pipeline, live publisher connector, alert service, or hosted operations environment.
- Do not run collection against a real publisher unless the exact source, endpoint, operation, evidence and current permission grant have been reviewed and authorized.
- Do not promote or publish an edition as part of routine recovery. Section 8's current release path is staging-only.
- Treat the SQLite registry and its backups as sensitive. They can contain publisher/permission metadata, review identities, editorial decisions, learning objects and edition artifacts.
- Backup files are created with owner-only permissions where supported, but they are not encrypted. Store them only in an access-controlled location.

## 1. Check the registry and pipeline state

~~~python
from sngso_engine import SQLiteRegistry, build_operational_health_report

with SQLiteRegistry("data/registry.sqlite") as registry:
    report = build_operational_health_report(registry)
    print(report)
~~~

Interpretation:

- 'healthy': observed runs have no detected stale/recent-failure issue and the retained audit chain verifies. This does not mean production is ready.
- 'degraded': audit verification failed, a queued/running record is stale, or a recent failed/rejected run exists.
- 'not_observed': no pipeline runs are recorded. Do not interpret an empty registry as a healthy operating service.
- 'production_ready' is always false in this report.

The report deliberately omits failure_detail and content payloads. Use authorized registry inspection for diagnosis; never paste credentials, private publisher correspondence, or full article bodies into logs or issue reports.

## 2. Create and verify a local backup

Create the parent directory first. The destination must not already exist.

~~~python
from sngso_engine import SQLiteRegistry, backup_registry_database

with SQLiteRegistry("data/registry.sqlite") as registry:
    result = backup_registry_database(
        registry,
        "backups/registry-before-maintenance.sqlite",
    )
    print(result)
~~~

The helper uses SQLite's online backup API, checks SQLite integrity, checks the schema version, verifies the retained audit sequence/hash chain, and only then installs the backup at the destination. It refuses to run inside an active registry transaction and refuses to overwrite an existing destination.

The returned audit checkpoint is a sequence/hash pair. Copy it to an independently controlled system if it will be used to detect later tail truncation. Keeping the only checkpoint beside the database does not protect against a privileged operator rewriting both.

## 3. Verify an existing backup

~~~python
from sngso_engine import verify_registry_backup

result = verify_registry_backup(
    "backups/registry-before-maintenance.sqlite",
)
print(result)
~~~

A verified backup should report SQLite integrity as 'ok' and audit-chain validity as true. Compare its audit checkpoint with the independently stored checkpoint when one exists.

## 4. Restore to a new path

Restore never overwrites an existing database. Choose a new destination path and verify it before directing any application to use it.

~~~python
from sngso_engine import restore_registry_backup

result = restore_registry_backup(
    "backups/registry-before-maintenance.sqlite",
    "recovery/registry-restored.sqlite",
)
print(result)
~~~

The helper requires the backup to match the current engine schema version, verifies it before copying, installs a new file, and verifies the restored copy. Do not replace an active database file while a process has it open. Stop the application, preserve the original file, restore to a new path, inspect the restored state, then change configuration only through an approved deployment procedure.

Automated tests cover restore to a temporary database. A production-like restore drill, recovery-point objective, recovery-time objective, encryption, off-site storage, retention and key management have not been established.

## 5. Classify a failed run before retrying

~~~python
from sngso_engine import classify_retry_policy

decision = classify_retry_policy(
    "network_timeout",
    attempt=1,
    max_attempts=3,
)
print(decision)
~~~

Only the explicitly recognized transient codes are eligible for bounded retry classification. Permission, identity, authentication, contract/schema, content-policy, unsupported-operation, inactive-source/endpoint and integrity failures are permanent. Unknown codes fail closed. The classifier does not execute a retry, wait, or schedule work.

A deliberate retry is a new attempt. Do not edit a terminal pipeline-run record. Use `create_pipeline_retry_run` only for a failed run with a known transient failure code, the exact prior attempt number, and explicit operator attestations that prior side effects were reconciled and permissions were rechecked. The helper creates a new queued run with the exact same immutable inputs; it does not execute it. The stage handler must recheck live permissions again at the point of use.

## 6. Duplicate submission protection

The idempotent pipeline-run helper derives a stable run ID from run kind, software version, configuration hash, ordered input hashes, initiator and attempt. Concurrent duplicate submissions for the same logical attempt converge on one record. A new attempt gets a new ID.

This prevents duplicate run-record creation. A synchronous injected-stage runner now claims queued records and prevents two workers from executing the same queued revision. It still cannot guarantee exactly-once external side effects: handlers must use their own durable idempotency keys and reconciliation. The runner does not retry or resume interrupted work.

## 7. Audit-chain limitations

The local audit chain is tamper-evident, not tamper-proof. A sufficiently privileged operator with direct database-file access can bypass SQLite triggers or rewrite the file. External checkpoint storage and an independent backup location are required to improve detection and recovery. No external checkpoint publisher or alert route is currently configured.

## 8. Escalation and stop conditions

Stop and do not retry automatically when:
- a permission grant is missing, suspended, expired, revoked, or out of endpoint scope;
- a source or endpoint is inactive;
- identity verification or reviewer/approver authorization fails;
- a schema, provenance, hash, or audit-chain check fails;
- the database reports corruption or an unexpected schema version;
- the staging state revision or current pointer differs from the expected value.

Preserve the original database and logs, record the run ID and bounded failure code, verify the audit chain, and involve the authorized maintainer. Do not bypass a failed gate to make a run green.

## Not yet available

There is no live monitoring dashboard, alert delivery, on-call rota, scheduler, worker pool, live source transport, automated backup schedule, external checkpoint publisher, encryption/key-management setup, retention automation, or production restore procedure. These require separate implementation and authorization.

## 9. Export and verify an audit checkpoint

After a successful audit-chain verification, write a checkpoint to a new file:

~~~python
from sngso_engine import SQLiteRegistry, write_external_audit_checkpoint

with SQLiteRegistry("data/registry.sqlite") as registry:
    checkpoint = write_external_audit_checkpoint(
        registry,
        "checkpoints/audit-tip-2026-11-22.json",
    )
    print(checkpoint)
~~~

Copy the checkpoint file to separately controlled storage. The local file alone is not an independent anchor.

Later, verify the current database against that stored checkpoint:

~~~python
from sngso_engine import SQLiteRegistry, verify_external_audit_checkpoint

with SQLiteRegistry("data/registry.sqlite") as registry:
    result = verify_external_audit_checkpoint(
        registry,
        "checkpoints/audit-tip-2026-11-22.json",
    )
    print(result)
~~~

Verification permits new events after the checkpoint, but detects a broken chain, truncation before the saved sequence, or a changed hash at that sequence. External copying and alerting are manual; no upload service is configured.

## 10. Run a local injected-stage validation

The synchronous runner is suitable only for local/pre-production validation with explicitly supplied stage functions. See docs/PIPELINE_WORKER_CONTRACT.md for the full interface and limitations.

~~~python
from datetime import datetime, timezone

from sngso_engine import (
    PipelineStage,
    SQLiteRegistry,
    create_pipeline_run_idempotently,
    execute_pipeline_run,
    sha256_fingerprint,
)

context = {"fixture": "no-network", "edition_date": "2026-11-22"}
with SQLiteRegistry("data/registry.sqlite") as registry:
    queued = create_pipeline_run_idempotently(
        registry,
        {
            "schema_version": "1.0.0",
            "status": "queued",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "software_version": "engine/0.1.0",
            "config_hash": "sha256:" + "a" * 64,
            "input_hashes": [sha256_fingerprint(context)],
            "initiated_by": "local-operator",
        },
        run_kind="local-validation",
        actor="local-operator",
        reason="Create local validation run.",
    )
    result = execute_pipeline_run(
        registry,
        queued["record"]["id"],
        input_context=context,
        stages=[
            PipelineStage("validate-fixture", lambda stage_context: {"valid": True}),
            PipelineStage("prepare-result", lambda stage_context: {"prepared": True}),
        ],
        actor="local-worker",
        reason="Run local fixture validation.",
    )
    print(result["status"], result["stage_results"])
~~~

Use only synthetic or otherwise authorized inputs. Do not connect a live publisher handler. The runner does not resume a running record, persist raw stage outputs, retry, supervise processes, or publish. If a process stops mid-run, preserve the record and reconcile any possible external side effects before creating a deliberate new attempt.

### Deliberately create a retry attempt

This is an operator decision, not an automatic retry. Review the failed run's stage results and investigate whether any external side effect may already have occurred. Confirm current permission status. The helper rejects permanent and unknown failure codes and verifies that the supplied previous attempt matches the failed run's deterministic identity.

~~~python
from sngso_engine import SQLiteRegistry, create_pipeline_retry_run

with SQLiteRegistry("data/registry.sqlite") as registry:
    retry = create_pipeline_retry_run(
        registry,
        failed_run_id="pipeline-run:...",
        run_kind="daily-edition",
        previous_attempt=1,
        recovery_confirmed=True,
        permissions_rechecked=True,
        actor="authorized-local-operator",
        reason="Reviewed prior stage outcomes and reconciled possible side effects.",
    )
    print(retry["record"]["id"], retry["record"]["status"])
~~~

The two boolean attestations are explicit operator assertions recorded with the retry creation reason. They are not cryptographic identity proof and do not replace fresh permission verification inside every handler. A retry is a new queued attempt, not a command to execute or publish.

